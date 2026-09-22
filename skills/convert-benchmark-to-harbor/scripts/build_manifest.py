#!/usr/bin/env python3
"""Serialize one task directory into a NeoSigma EvalManifest JSON payload."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import stat
import sys
from pathlib import Path, PurePosixPath


MEDIA_TYPE_OVERRIDES = {
    ".md": "text/markdown",
    ".sh": "text/x-shellscript",
    ".toml": "application/toml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_directory", type=Path)
    parser.add_argument("--source-task-id", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _media_type(path: Path) -> str:
    override = MEDIA_TYPE_OVERRIDES.get(path.suffix.lower())
    if override is not None:
        return override
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _relative_posix(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
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


def build_manifest(task_directory: Path, source_task_id: str) -> dict[str, object]:
    if not source_task_id or len(source_task_id) > 512:
        raise ValueError("source task ID must contain 1 to 512 characters")

    root = task_directory.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"task directory is not a directory: {task_directory}")

    files: list[dict[str, object]] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directory_names):
            candidate = current_path / name
            if candidate.is_symlink():
                raise ValueError(
                    f"task contains a symlink: {_relative_posix(candidate, root)}"
                )
        for name in sorted(file_names):
            candidate = current_path / name
            relative = _relative_posix(candidate, root)
            file_stat = candidate.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                raise ValueError(f"task contains a symlink: {relative}")
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"task contains a non-regular file: {relative}")
            files.append(
                {
                    "path": relative,
                    "content": base64.b64encode(candidate.read_bytes()).decode("ascii"),
                    "executable": bool(
                        file_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    ),
                    "media_type": _media_type(candidate),
                }
            )

    if not files:
        raise ValueError("task directory contains no regular files")
    files.sort(key=lambda item: str(item["path"]))
    return {"version": 1, "source_task_id": source_task_id, "files": files}


def main() -> int:
    arguments = _arguments()
    try:
        root = arguments.task_directory.resolve(strict=True)
        if arguments.output is not None:
            output = arguments.output.resolve()
            if output == root or root in output.parents:
                raise ValueError("manifest output must be outside the task directory")
        manifest = build_manifest(arguments.task_directory, arguments.source_task_id)
        encoded = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if arguments.output is None:
            sys.stdout.write(encoded)
        else:
            arguments.output.write_text(encoded, encoding="utf-8")
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
