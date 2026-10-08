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
sys.modules[SPEC.name] = BUILD_MANIFEST
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

    def test_manifest_rejects_paths_the_service_manifest_rejects(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            task = Path(temporary_directory) / "task"
            task.mkdir()
            (task / "task.toml").write_text("[task]\n")
            (task / "bad\\name.txt").write_text("backslash")
            (task / "tab\tname.txt").write_text("control character")
            (task / ("x" * 255) / ("y" * 255)).mkdir(parents=True)
            (task / ("x" * 255) / ("y" * 255) / "long").write_text("over 512")

            with self.assertRaises(BUILD_MANIFEST.ManifestError) as raised:
                BUILD_MANIFEST.build_manifest(task, "source/1")

            problems = {(item.code, item.path) for item in raised.exception.problems}
            self.assertEqual(
                problems,
                {
                    ("invalid_path", "bad\\name.txt"),
                    ("invalid_path", "tab\tname.txt"),
                    ("invalid_path", f"{'x' * 255}/{'y' * 255}/long"),
                },
            )

    def test_file_swapped_to_a_fifo_after_inspection_fails_without_blocking(
        self,
    ) -> None:
        code = """
import importlib.util, os, sys
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("build_manifest", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
root = Path(sys.argv[2])
payload = root / "payload"
payload.write_bytes(b"original")
original_open = os.open
def swap(name, flags, *args, **kwargs):
    if name == "payload" and not payload.is_fifo():
        payload.unlink()
        os.mkfifo(payload)
    return original_open(name, flags, *args, **kwargs)
with patch.object(module.os, "open", side_effect=swap):
    try:
        module.build_manifest(root, "source/1")
    except module.ManifestError as error:
        print([problem.code for problem in error.problems])
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            result = subprocess.run(
                [sys.executable, "-c", code, str(SCRIPT), temporary_directory],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "['special_file']")

    def test_manifest_reports_every_unrepresentable_entry_in_one_run(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            (task / "environment" / "cache").mkdir(parents=True)
            (task / "task.toml").write_text("[task]\n")
            (task / "link").symlink_to(temporary)
            os.mkfifo(task / "pipe")
            setuid = task / "setuid-tool"
            setuid.write_text("#!/bin/sh\n")
            setuid.chmod(0o4755)
            oversized = task / "large.bin"
            with oversized.open("wb") as stream:
                stream.truncate(BUILD_MANIFEST.MAX_FILE_BYTES + 1)

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(task), "--source-task-id", "s"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        reported = sorted(line.split(": ")[1] for line in result.stderr.splitlines())
        self.assertEqual(
            reported,
            [
                "empty_directory",
                "file_too_large",
                "special_file",
                "symlink",
                "unrepresentable_mode",
            ],
        )

    def test_manifest_digest_matches_the_service_algorithm(self) -> None:
        manifest = {
            "version": 1,
            "source_task_id": "source/example",
            "files": [
                {
                    "path": "tests/test.sh",
                    "content": base64.b64encode(b"#!/bin/sh\nexit 0\n").decode(),
                    "executable": True,
                    "media_type": "text/x-shellscript",
                },
                {
                    "path": "task.toml",
                    "content": base64.b64encode(b"[task]\n").decode(),
                    "executable": False,
                    "media_type": "application/toml",
                },
                {
                    "path": "bin.dat",
                    "content": base64.b64encode(b"\x00\xff").decode(),
                    "executable": False,
                    "media_type": "application/octet-stream",
                },
            ],
        }

        # Computed by neosigma_evals.manifest.EvalManifest.digest() for these bytes.
        self.assertEqual(
            BUILD_MANIFEST.manifest_digest(manifest),
            "sha256:97266c518379b76945ac1acdf3ef88775e56cf914aacf74798fd82b04370ac11",
        )

    def test_cli_reports_the_manifest_digest_and_skill_bundle(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            task.mkdir()
            (task / "task.toml").write_text("[task]\n")
            output = temporary / "manifest.json"

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

            summary = json.loads(result.stdout)
            manifest = json.loads(output.read_text())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            summary["manifest_digest"], BUILD_MANIFEST.manifest_digest(manifest)
        )
        self.assertEqual(
            summary["skill_bundle_digest"], BUILD_MANIFEST.skill_bundle_digest()
        )
        self.assertEqual(summary["file_count"], 1)

    def test_skill_digest_ignores_caches_and_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            skill = Path(temporary_directory)
            (skill / "scripts").mkdir()
            (skill / "SKILL.md").write_text("skill")
            (skill / "scripts" / "tool.py").write_text("tool")
            before = BUILD_MANIFEST.skill_bundle_digest(skill)
            (skill / "scripts" / "__pycache__").mkdir()
            (skill / "scripts" / "__pycache__" / "tool.pyc").write_bytes(b"cache")
            (skill / "scripts" / ".ruff_cache").mkdir()
            (skill / "scripts" / ".ruff_cache" / "entry").write_text("cache")
            (skill / ".DS_Store").write_bytes(b"finder")
            self.assertEqual(BUILD_MANIFEST.skill_bundle_digest(skill), before)
            (skill / "SKILL.md").write_text("changed skill")
            self.assertNotEqual(BUILD_MANIFEST.skill_bundle_digest(skill), before)

    def test_cli_prints_the_skill_digest_without_a_task(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--skill-digest"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), BUILD_MANIFEST.skill_bundle_digest())


if __name__ == "__main__":
    unittest.main()
