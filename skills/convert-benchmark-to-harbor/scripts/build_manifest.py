#!/usr/bin/env python3
"""Serialize one task directory into a NeoSigma EvalManifest JSON payload."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import stat
import sys
from pathlib import Path, PurePosixPath


MEDIA_TYPE_OVERRIDES = {
    ".css": "text/css",
    ".csv": "text/csv",
    ".gif": "image/gif",
    ".html": "text/html",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "application/javascript",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".py": "text/x-python",
    ".sh": "text/x-shellscript",
    ".svg": "image/svg+xml",
    ".toml": "application/toml",
    ".txt": "text/plain",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".zip": "application/zip",
}
MAX_FILES = 1_000
MAX_FILE_BYTES = 4_194_304
MAX_TOTAL_BYTES = 20_971_520
MAX_PATH_DEPTH = 16
MAX_ENTRIES = 2_000
READ_CHUNK_BYTES = 65_536


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_directory", type=Path)
    parser.add_argument("--source-task-id", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _media_type(path: Path) -> str:
    return MEDIA_TYPE_OVERRIDES.get(path.suffix.lower(), "application/octet-stream")


def _relative_posix(parts: tuple[str, ...]) -> str:
    relative = PurePosixPath(*parts).as_posix()
    parsed = PurePosixPath(relative)
    if (
        not relative
        or relative == "."
        or relative != parsed.as_posix()
        or parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"non-canonical task path: {relative!r}")
    return relative


def _secure_open_flags(*, directory: bool = False) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("secure manifest traversal requires O_NOFOLLOW and O_DIRECTORY")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if directory:
        flags |= os.O_DIRECTORY
    return flags


def _absolute_path(path: Path) -> Path:
    """Return a lexical absolute path without following any filesystem links."""
    return Path(os.path.abspath(os.fspath(path)))


def _open_directory(path: Path) -> int:
    """Open every component of an absolute directory path without following links."""
    absolute = _absolute_path(path)
    descriptor = os.open("/", _secure_open_flags(directory=True))
    try:
        for part in absolute.parts[1:]:
            child = os.open(
                part,
                _secure_open_flags(directory=True),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_atomic(path: Path, content: bytes) -> None:
    """Replace one output entry without following it or any parent symlink."""
    absolute = _absolute_path(path)
    if absolute.name in {"", ".", ".."}:
        raise ValueError("manifest output must name a file")
    parent_fd = _open_directory(absolute.parent)
    temporary_name = f".{absolute.name}.{secrets.token_hex(12)}.tmp"
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("failed to write manifest output")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            absolute.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(parent_fd)


def _read_bounded_regular_file(
    *, parent_fd: int, name: str, relative: str, remaining_total_bytes: int
) -> tuple[bytes, int]:
    descriptor = os.open(name, _secure_open_flags(), dir_fd=parent_fd)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"task contains a non-regular file: {relative}")
        if file_stat.st_size > MAX_FILE_BYTES:
            raise ValueError(f"task file exceeds the byte limit: {relative}")
        if file_stat.st_size > remaining_total_bytes:
            raise ValueError("task exceeds the total byte limit")

        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(
                descriptor,
                min(READ_CHUNK_BYTES, MAX_FILE_BYTES - size + 1),
            )
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ValueError(f"task file exceeds the byte limit: {relative}")
            if size > remaining_total_bytes:
                raise ValueError("task exceeds the total byte limit")
            chunks.append(chunk)
        return b"".join(chunks), file_stat.st_mode
    finally:
        os.close(descriptor)


def _collect_files(
    *,
    directory_fd: int,
    parts: tuple[str, ...],
    files: list[dict[str, object]],
    total_bytes: int,
    entry_count: list[int],
) -> int:
    names: list[str] = []
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            entry_count[0] += 1
            if entry_count[0] > MAX_ENTRIES:
                raise ValueError("task exceeds the directory entry limit")
            names.append(entry.name)

    for name in sorted(names):
        relative_parts = (*parts, name)
        relative = _relative_posix(relative_parts)
        if len(relative_parts) > MAX_PATH_DEPTH:
            raise ValueError(f"task entry exceeds the path depth limit: {relative}")
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise ValueError(f"task contains a symlink: {relative}")
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd = os.open(
                name, _secure_open_flags(directory=True), dir_fd=directory_fd
            )
            try:
                total_bytes = _collect_files(
                    directory_fd=child_fd,
                    parts=relative_parts,
                    files=files,
                    total_bytes=total_bytes,
                    entry_count=entry_count,
                )
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            raise ValueError(f"task contains a non-regular file: {relative}")
        if len(files) >= MAX_FILES:
            raise ValueError("task exceeds the file count limit")

        content, file_mode = _read_bounded_regular_file(
            parent_fd=directory_fd,
            name=name,
            relative=relative,
            remaining_total_bytes=MAX_TOTAL_BYTES - total_bytes,
        )
        total_bytes += len(content)
        files.append(
            {
                "path": relative,
                "content": base64.b64encode(content).decode("ascii"),
                "executable": bool(
                    file_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                ),
                "media_type": _media_type(Path(name)),
            }
        )
    return total_bytes


def build_manifest(task_directory: Path, source_task_id: str) -> dict[str, object]:
    if not source_task_id or len(source_task_id) > 512:
        raise ValueError("source task ID must contain 1 to 512 characters")

    files: list[dict[str, object]] = []
    root_fd = _open_directory(task_directory)
    try:
        _collect_files(
            directory_fd=root_fd,
            parts=(),
            files=files,
            total_bytes=0,
            entry_count=[0],
        )
    finally:
        os.close(root_fd)

    if not files:
        raise ValueError("task directory contains no regular files")
    files.sort(key=lambda item: str(item["path"]))
    return {"version": 1, "source_task_id": source_task_id, "files": files}


def main() -> int:
    arguments = _arguments()
    try:
        root = _absolute_path(arguments.task_directory)
        if arguments.output is not None:
            output = _absolute_path(arguments.output)
            if output == root or root in output.parents:
                raise ValueError("manifest output must be outside the task directory")
        manifest = build_manifest(arguments.task_directory, arguments.source_task_id)
        encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if arguments.output is None:
            sys.stdout.write(encoded)
        else:
            _write_atomic(arguments.output, encoded.encode("utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
