#!/usr/bin/env python3
"""Write the exact bytes of one EvalManifest into a new task directory.

Smoke runs use this output so the tested files are the files that will be
published. Files are written with mode 0755 or 0644, as NeoSigma materializes
them, and the manifest digest is printed for the conversion record.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import os
import sys
from pathlib import Path, PurePosixPath


def _load_build_manifest():
    path = Path(__file__).with_name("build_manifest.py")
    spec = importlib.util.spec_from_file_location("build_manifest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load build_manifest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILD_MANIFEST = _load_build_manifest()
Problem = BUILD_MANIFEST.Problem
ManifestError = BUILD_MANIFEST.ManifestError
MANIFEST_KEYS = {"version", "source_task_id", "files"}
FILE_KEYS = {"path", "content", "executable", "media_type"}
MAX_MEDIA_TYPE_CHARACTERS = 128


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--expect-digest",
        help="Fail unless the manifest digest equals this value.",
    )
    return parser.parse_args()


def _media_type_problem(media_type: object) -> str | None:
    if not isinstance(media_type, str) or not media_type:
        return "media_type must be a non-empty string"
    if len(media_type) > MAX_MEDIA_TYPE_CHARACTERS:
        return "media_type exceeds 128 characters"
    essence = media_type.partition(";")[0].strip()
    major, separator, subtype = essence.partition("/")
    if (
        not major
        or separator != "/"
        or not subtype
        or "/" in subtype
        or any(character.isspace() for character in essence)
        or any(ord(character) < 32 or ord(character) == 127 for character in media_type)
    ):
        return "media_type must be a valid HTTP media type"
    return None


def _file_problems(index: int, item: object) -> list[Problem]:
    location = f"files[{index}]"
    if not isinstance(item, dict) or set(item) - FILE_KEYS or "path" not in item:
        return [
            Problem(
                "invalid_manifest", location, "file entry has unknown or missing keys"
            )
        ]
    problems: list[Problem] = []
    path = item["path"]
    path_problem = (
        BUILD_MANIFEST.path_problem(path)
        if isinstance(path, str)
        else "path must be text"
    )
    if path_problem is not None:
        problems.append(Problem("invalid_path", location, path_problem))
    if not isinstance(item.get("executable", False), bool):
        problems.append(
            Problem("invalid_manifest", location, "executable must be a boolean")
        )
    media_problem = _media_type_problem(
        item.get("media_type", "application/octet-stream")
    )
    if media_problem is not None:
        problems.append(Problem("invalid_manifest", location, media_problem))
    try:
        base64.b64decode(item.get("content", ""), validate=True)
    except (binascii.Error, TypeError, ValueError):
        problems.append(Problem("invalid_manifest", location, "content must be base64"))
    return problems


def validate_manifest(manifest: object) -> None:
    """Apply the service manifest rules and admission limits before writing anything."""
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ManifestError(
            [
                Problem(
                    "invalid_manifest",
                    "",
                    "manifest must have version, source_task_id and files",
                )
            ]
        )
    problems: list[Problem] = []
    source_task_id = manifest["source_task_id"]
    if manifest["version"] != BUILD_MANIFEST.MANIFEST_VERSION:
        problems.append(
            Problem("invalid_manifest", "version", "manifest version must be 1")
        )
    if not isinstance(source_task_id, str) or not (
        0 < len(source_task_id) <= BUILD_MANIFEST.MAX_SOURCE_TASK_ID_CHARACTERS
    ):
        problems.append(
            Problem(
                "invalid_source_task_id",
                "source_task_id",
                "source task ID must contain 1 to 512 characters",
            )
        )
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ManifestError(
            [*problems, Problem("no_files", "files", "manifest has no files")]
        )
    for index, item in enumerate(files):
        problems.extend(_file_problems(index, item))
    if problems:
        raise ManifestError(problems)
    _structure_problems(files)


def _structure_problems(files: list[dict[str, object]]) -> None:
    paths = [str(item["path"]) for item in files]
    path_set = set(paths)
    problems: list[Problem] = []
    if len(paths) != len(path_set):
        problems.append(
            Problem("duplicate_path", "files", "manifest file paths must be unique")
        )
    for path in paths:
        parents = PurePosixPath(path).parents
        if any(parent.as_posix() in path_set for parent in parents):
            problems.append(
                Problem(
                    "path_collision",
                    path,
                    f"a file is also used as a directory: {path}",
                )
            )
    sizes = [len(base64.b64decode(str(item.get("content", "")))) for item in files]
    if len(files) > BUILD_MANIFEST.MAX_FILES:
        problems.append(
            Problem("too_many_files", "files", "manifest exceeds the file count limit")
        )
    if any(len(path.split("/")) > BUILD_MANIFEST.MAX_PATH_DEPTH for path in paths):
        problems.append(
            Problem("path_too_deep", "files", "manifest exceeds the path depth limit")
        )
    if any(size > BUILD_MANIFEST.MAX_FILE_BYTES for size in sizes):
        problems.append(
            Problem(
                "file_too_large",
                "files",
                "manifest contains a file over the byte limit",
            )
        )
    if sum(sizes) > BUILD_MANIFEST.MAX_TOTAL_BYTES:
        problems.append(
            Problem("total_too_large", "files", "manifest exceeds the total byte limit")
        )
    if problems:
        raise ManifestError(problems)


def materialize(manifest: dict[str, object], output_directory: Path) -> None:
    """Create a new directory holding exactly the manifest files."""
    output_directory.mkdir(mode=0o755)
    for item in manifest["files"]:  # type: ignore[union-attr]
        destination = output_directory.joinpath(*PurePosixPath(item["path"]).parts)
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        mode = 0o755 if item.get("executable", False) else 0o644
        descriptor = os.open(destination, flags, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(base64.b64decode(item.get("content", ""), validate=True))
        os.chmod(destination, mode)


def main() -> int:
    """Validate, materialize and print the manifest digest."""
    arguments = _arguments()
    try:
        manifest = json.loads(arguments.manifest.read_bytes())
        validate_manifest(manifest)
        digest = BUILD_MANIFEST.manifest_digest(manifest)
        if arguments.expect_digest is not None and digest != arguments.expect_digest:
            raise ManifestError(
                [
                    Problem(
                        "digest_mismatch",
                        "",
                        f"manifest digest is {digest}, "
                        f"expected {arguments.expect_digest}",
                    )
                ]
            )
        materialize(manifest, arguments.output_directory)
    except ManifestError as error:
        BUILD_MANIFEST.report_problems(error)
        return 2
    except (OSError, ValueError) as error:
        print(f"error: os_error: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "manifest_digest": digest,
                "task_directory": str(arguments.output_directory),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
