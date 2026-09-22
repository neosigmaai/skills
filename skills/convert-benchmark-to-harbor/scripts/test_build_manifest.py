from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("build_manifest.py")
SPEC = importlib.util.spec_from_file_location("build_manifest", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load build_manifest.py")
BUILD_MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_MANIFEST)


class BuildManifestTests(unittest.TestCase):
    def test_cli_preserves_nested_binary_bytes_media_types_and_executable_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            (task / "tests").mkdir(parents=True)
            (task / "task.toml").write_text("[task]\nname='source/example'\n")
            binary = b"\x00\xffbenchmark\n"
            (task / "fixture.bin").write_bytes(binary)
            (task / "portable.unknown-extension").write_text("stable media type")
            verifier = task / "tests" / "test.sh"
            verifier.write_bytes(b"#!/bin/sh\nprintf '1' > /logs/verifier/reward.txt\n")
            verifier.chmod(0o755)
            output = temporary / "manifest.json"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(task),
                    "--source-task-id",
                    "source/example",
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(output.read_text())
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(manifest["source_task_id"], "source/example")
            files = {item["path"]: item for item in manifest["files"]}
            self.assertEqual(
                set(files),
                {
                    "fixture.bin",
                    "portable.unknown-extension",
                    "task.toml",
                    "tests/test.sh",
                },
            )
            self.assertEqual(base64.b64decode(files["fixture.bin"]["content"]), binary)
            self.assertEqual(
                files["fixture.bin"]["media_type"], "application/octet-stream"
            )
            self.assertFalse(files["fixture.bin"]["executable"])
            self.assertEqual(files["task.toml"]["media_type"], "application/toml")
            self.assertEqual(files["tests/test.sh"]["media_type"], "text/x-shellscript")
            self.assertTrue(files["tests/test.sh"]["executable"])
            self.assertEqual(
                files["portable.unknown-extension"]["media_type"],
                "application/octet-stream",
            )

    def test_cli_rejects_symlinks_instead_of_dereferencing_host_content(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            task.mkdir()
            outside = temporary / "secret.txt"
            outside.write_text("must not be copied")
            (task / "linked.txt").symlink_to(outside)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(task),
                    "--source-task-id",
                    "source/1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("task contains a symlink: linked.txt", result.stderr)
            self.assertNotIn("must not be copied", result.stdout)

    def test_descriptor_read_rejects_a_file_swapped_to_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            task.mkdir()
            payload = task / "payload.txt"
            payload.write_text("safe")
            outside = temporary / "secret.txt"
            outside.write_text("must not be copied")
            original_stat = BUILD_MANIFEST.os.stat
            swapped = False

            def swap_after_stat(path, *args, **kwargs):
                nonlocal swapped
                result = original_stat(path, *args, **kwargs)
                if path == "payload.txt" and kwargs.get("dir_fd") is not None:
                    if not swapped:
                        payload.unlink()
                        payload.symlink_to(outside)
                        swapped = True
                return result

            with mock.patch.object(
                BUILD_MANIFEST.os, "stat", side_effect=swap_after_stat
            ):
                with self.assertRaises(OSError):
                    BUILD_MANIFEST.build_manifest(task, "source/1")

            self.assertTrue(swapped)

    def test_cli_rejects_an_oversized_sparse_file_before_encoding(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            task = Path(temporary_directory) / "task"
            task.mkdir()
            oversized = task / "oversized.bin"
            with oversized.open("wb") as stream:
                stream.truncate(BUILD_MANIFEST.MAX_FILE_BYTES + 1)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(task),
                    "--source-task-id",
                    "source/1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("task file exceeds the byte limit", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_cli_rejects_output_inside_task_to_keep_retries_immutable(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            task = Path(temporary_directory) / "task"
            task.mkdir()
            (task / "task.toml").write_text("[task]\nname='source/example'\n")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(task),
                    "--source-task-id",
                    "source/1",
                    "--output",
                    str(task / "manifest.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("manifest output must be outside", result.stderr)
            self.assertFalse((task / "manifest.json").exists())

    def test_cli_replaces_an_output_symlink_without_writing_its_target(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            task.mkdir()
            (task / "task.toml").write_text("[task]\nname='source/example'\n")
            protected = temporary / "protected.txt"
            protected.write_text("do not overwrite")
            output = temporary / "manifest.json"
            output.symlink_to(protected)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(task),
                    "--source-task-id",
                    "source/1",
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(protected.read_text(), "do not overwrite")
            self.assertFalse(output.is_symlink())
            self.assertEqual(
                json.loads(output.read_text())["source_task_id"], "source/1"
            )

    def test_cli_rejects_a_symlink_in_the_task_root_ancestry(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            real_parent = temporary / "real"
            task = real_parent / "task"
            task.mkdir(parents=True)
            (task / "task.toml").write_text("[task]\nname='source/example'\n")
            linked_parent = temporary / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(linked_parent / "task"),
                    "--source-task-id",
                    "source/1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_manifest_rejects_deep_empty_directory_trees(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            task = Path(temporary_directory) / "task"
            current = task
            for index in range(BUILD_MANIFEST.MAX_PATH_DEPTH + 1):
                current /= f"level-{index}"
            current.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "path depth limit"):
                BUILD_MANIFEST.build_manifest(task, "source/1")

    def test_manifest_bounds_directory_entries_before_collecting_files(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            task = Path(temporary_directory) / "task"
            task.mkdir()
            for index in range(3):
                (task / f"empty-{index}").mkdir()

            with mock.patch.object(BUILD_MANIFEST, "MAX_ENTRIES", 2):
                with self.assertRaisesRegex(ValueError, "directory entry limit"):
                    BUILD_MANIFEST.build_manifest(task, "source/1")


if __name__ == "__main__":
    unittest.main()
