#!/usr/bin/env python3
"""Serialize one task directory into a NeoSigma EvalManifest JSON payload."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import secrets
import stat
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import NamedTuple


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
# Defaults of the NeoSigma service admission limits; the service is authoritative.
MAX_FILES = 1_000
MAX_FILE_BYTES = 4_194_304
MAX_TOTAL_BYTES = 20_971_520
MAX_PATH_DEPTH = 16
MAX_PATH_CHARACTERS = 512
MAX_SOURCE_TASK_ID_CHARACTERS = 512
MAX_ENTRIES = 2_000
READ_CHUNK_BYTES = 65_536
MANIFEST_VERSION = 1
EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
UNREPRESENTABLE_MODE_BITS = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
SKILL_DIRECTORY = Path(__file__).resolve().parent.parent
# Caches and hidden files are not part of the skill.
SKILL_BUNDLE_IGNORED = {"__pycache__"}


class Problem(NamedTuple):
    """One reason a task directory cannot become a faithful manifest."""

    code: str
    path: str
    message: str


class ManifestError(ValueError):
    """The task directory cannot be represented; every problem is reported."""

    def __init__(self, problems: list[Problem]) -> None:
        """Keep every problem so one run reports all of them."""
        self.problems = problems
        super().__init__("; ".join(problem.message for problem in problems))


def _fail(code: str, path: str, message: str) -> ManifestError:
    return ManifestError([Problem(code, path, message)])


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_directory", type=Path, nargs="?")
    parser.add_argument("--source-task-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skill-digest",
        action="store_true",
        help="Print the digest of the skill files that are running, then exit.",
    )
    arguments = parser.parse_args()
    if not arguments.skill_digest and (
        arguments.task_directory is None or arguments.source_task_id is None
    ):
        parser.error("task_directory and --source-task-id are required")
    return arguments


def _media_type(name: str) -> str:
    return MEDIA_TYPE_OVERRIDES.get(
        PurePosixPath(name).suffix.lower(), "application/octet-stream"
    )


def path_problem(relative: str) -> str | None:
    """Return why the service manifest model would reject this path, if it would."""
    parsed = PurePosixPath(relative)
    if (
        not relative
        or relative == "."
        or relative != parsed.as_posix()
        or parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        return f"non-canonical task path: {relative!r}"
    if "\\" in relative:
        return f"task path contains a backslash: {relative!r}"
    if any(unicodedata.category(character) == "Cc" for character in relative):
        return f"task path contains a control character: {relative!r}"
    if len(relative) > MAX_PATH_CHARACTERS:
        return f"task path exceeds {MAX_PATH_CHARACTERS} characters: {relative!r}"
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError:
        return f"task path is not valid UTF-8: {relative!r}"
    return None


def _secure_open_flags(*, directory: bool = False) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise OSError("secure manifest traversal requires O_NOFOLLOW and O_DIRECTORY")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if directory:
        flags |= os.O_DIRECTORY
    else:
        # A file swapped to a FIFO or device after inspection must not block or
        # take a controlling terminal; fstat then rejects it.
        flags |= os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0)
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
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=parent_fd)
        os.close(parent_fd)


def _read_bounded_regular_file(
    *, parent_fd: int, name: str, relative: str, remaining_total_bytes: int
) -> tuple[bytes, int]:
    descriptor = os.open(name, _secure_open_flags(), dir_fd=parent_fd)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise _fail(
                "special_file",
                relative,
                f"task contains a non-regular file: {relative}",
            )
        if file_stat.st_size > MAX_FILE_BYTES:
            raise _fail(
                "file_too_large",
                relative,
                f"task file exceeds the byte limit: {relative}",
            )
        if file_stat.st_size > remaining_total_bytes:
            raise _fail(
                "total_too_large", relative, "task exceeds the total byte limit"
            )

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
                raise _fail(
                    "file_too_large",
                    relative,
                    f"task file exceeds the byte limit: {relative}",
                )
            if size > remaining_total_bytes:
                raise _fail(
                    "total_too_large", relative, "task exceeds the total byte limit"
                )
            chunks.append(chunk)
        return b"".join(chunks), file_stat.st_mode
    finally:
        os.close(descriptor)


class _Traversal:
    """Files read so far and every problem found while walking the task."""

    def __init__(self) -> None:
        """Start an empty traversal."""
        self.files: list[dict[str, object]] = []
        self.problems: list[Problem] = []
        self.total_bytes = 0
        self.entries = 0
        self.total_exceeded = False


def _record(traversal: _Traversal, error: ManifestError) -> None:
    for problem in error.problems:
        if problem.code == "total_too_large":
            if traversal.total_exceeded:
                continue
            traversal.total_exceeded = True
        traversal.problems.append(problem)


def _entry_problem(relative: str, entry_stat: os.stat_result) -> Problem | None:
    """Return why one non-directory entry cannot be stored as a manifest file."""
    if stat.S_ISLNK(entry_stat.st_mode):
        return Problem("symlink", relative, f"task contains a symlink: {relative}")
    if not stat.S_ISREG(entry_stat.st_mode):
        return Problem(
            "special_file", relative, f"task contains a non-regular file: {relative}"
        )
    if entry_stat.st_mode & UNREPRESENTABLE_MODE_BITS:
        return Problem(
            "unrepresentable_mode",
            relative,
            "task file has setuid, setgid or sticky bits, which a manifest "
            f"cannot represent: {relative}",
        )
    return None


def _list_directory(directory_fd: int, traversal: _Traversal) -> list[str]:
    names: list[str] = []
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            traversal.entries += 1
            if traversal.entries > MAX_ENTRIES:
                raise _fail(
                    "too_many_entries", "", "task exceeds the directory entry limit"
                )
            names.append(entry.name)
    return sorted(names)


def _add_file(
    *, directory_fd: int, name: str, relative: str, traversal: _Traversal
) -> None:
    if len(traversal.files) >= MAX_FILES:
        raise _fail("too_many_files", relative, "task exceeds the file count limit")
    if traversal.total_exceeded:
        return
    try:
        content, file_mode = _read_bounded_regular_file(
            parent_fd=directory_fd,
            name=name,
            relative=relative,
            remaining_total_bytes=MAX_TOTAL_BYTES - traversal.total_bytes,
        )
    except ManifestError as error:
        _record(traversal, error)
        return
    traversal.total_bytes += len(content)
    traversal.files.append(
        {
            "path": relative,
            "content": base64.b64encode(content).decode("ascii"),
            "executable": bool(file_mode & EXECUTABLE_BITS),
            "media_type": _media_type(name),
        }
    )


def _collect_files(
    *, directory_fd: int, parts: tuple[str, ...], traversal: _Traversal
) -> None:
    names = _list_directory(directory_fd, traversal)
    if not names and parts:
        relative = PurePosixPath(*parts).as_posix()
        traversal.problems.append(
            Problem(
                "empty_directory",
                relative,
                "task contains an empty directory, which a manifest cannot "
                f"represent: {relative}",
            )
        )

    for name in names:
        relative_parts = (*parts, name)
        relative = PurePosixPath(*relative_parts).as_posix()
        if len(relative_parts) > MAX_PATH_DEPTH:
            raise _fail(
                "path_too_deep",
                relative,
                f"task entry exceeds the path depth limit: {relative}",
            )
        problem = path_problem(relative)
        if problem is not None:
            traversal.problems.append(Problem("invalid_path", relative, problem))
            continue
        entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(entry_stat.st_mode):
            child_fd = os.open(
                name, _secure_open_flags(directory=True), dir_fd=directory_fd
            )
            try:
                _collect_files(
                    directory_fd=child_fd, parts=relative_parts, traversal=traversal
                )
            finally:
                os.close(child_fd)
            continue
        problem = _entry_problem(relative, entry_stat)
        if problem is not None:
            traversal.problems.append(problem)
            continue
        _add_file(
            directory_fd=directory_fd, name=name, relative=relative, traversal=traversal
        )


def build_manifest(task_directory: Path, source_task_id: str) -> dict[str, object]:
    """Return the manifest for every regular file, or report every problem."""
    if not source_task_id or len(source_task_id) > MAX_SOURCE_TASK_ID_CHARACTERS:
        raise _fail(
            "invalid_source_task_id",
            "",
            "source task ID must contain 1 to 512 characters",
        )

    traversal = _Traversal()
    root_fd = _open_directory(task_directory)
    try:
        _collect_files(directory_fd=root_fd, parts=(), traversal=traversal)
    finally:
        os.close(root_fd)

    if traversal.problems:
        raise ManifestError(traversal.problems)
    if not traversal.files:
        raise _fail("no_files", "", "task directory contains no regular files")
    traversal.files.sort(key=lambda item: str(item["path"]))
    return {
        "version": MANIFEST_VERSION,
        "source_task_id": source_task_id,
        "files": traversal.files,
    }


def manifest_digest(manifest: dict[str, object]) -> str:
    """Return the identity the NeoSigma service computes for the same manifest."""
    files = []
    for item in manifest["files"]:  # type: ignore[union-attr]
        content = base64.b64decode(item["content"], validate=True)
        files.append(
            {
                "path": item["path"],
                "executable": item["executable"],
                "media_type": item["media_type"],
                "size_bytes": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    payload = {
        "version": manifest["version"],
        "source_task_id": manifest["source_task_id"],
        "files": sorted(files, key=lambda item: item["path"]),
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def skill_bundle_digest(directory: Path = SKILL_DIRECTORY) -> str:
    """Identify the exact skill files that ran, whatever installed them."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if not path.is_file() or any(
            part.startswith(".") or part in SKILL_BUNDLE_IGNORED
            for part in relative.parts
        ):
            continue
        digest.update(relative.as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return "sha256:" + digest.hexdigest()


def report_problems(error: ManifestError) -> None:
    """Print one stable code and reason per problem."""
    for problem in error.problems:
        print(f"error: {problem.code}: {problem.message}", file=sys.stderr)


def main() -> int:
    """Serialize the task, or print every representation problem and exit 2."""
    arguments = _arguments()
    if arguments.skill_digest:
        sys.stdout.write(skill_bundle_digest() + "\n")
        return 0
    try:
        root = _absolute_path(arguments.task_directory)
        if arguments.output is not None:
            output = _absolute_path(arguments.output)
            if output == root or root in output.parents:
                raise _fail(
                    "output_inside_task",
                    "",
                    "manifest output must be outside the task directory",
                )
        manifest = build_manifest(arguments.task_directory, arguments.source_task_id)
        encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if arguments.output is None:
            sys.stdout.write(encoded)
        else:
            _write_atomic(arguments.output, encoded.encode("utf-8"))
            summary = {
                "manifest_digest": manifest_digest(manifest),
                "source_task_id": manifest["source_task_id"],
                "file_count": len(manifest["files"]),  # type: ignore[arg-type]
                "skill_bundle_digest": skill_bundle_digest(),
            }
            sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
    except ManifestError as error:
        report_problems(error)
        return 2
    except (OSError, ValueError) as error:
        print(f"error: os_error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
