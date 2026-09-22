from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("build_manifest.py")


class BuildManifestTests(unittest.TestCase):
    def test_cli_preserves_nested_binary_bytes_media_types_and_executable_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            (task / "tests").mkdir(parents=True)
            (task / "task.toml").write_text("[task]\nname='source/example'\n")
            binary = b"\x00\xffbenchmark\n"
            (task / "fixture.bin").write_bytes(binary)
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
            self.assertEqual(set(files), {"fixture.bin", "task.toml", "tests/test.sh"})
            self.assertEqual(base64.b64decode(files["fixture.bin"]["content"]), binary)
            self.assertEqual(
                files["fixture.bin"]["media_type"], "application/octet-stream"
            )
            self.assertFalse(files["fixture.bin"]["executable"])
            self.assertEqual(files["task.toml"]["media_type"], "application/toml")
            self.assertEqual(files["tests/test.sh"]["media_type"], "text/x-shellscript")
            self.assertTrue(files["tests/test.sh"]["executable"])

    def test_cli_rejects_symlinks_instead_of_dereferencing_host_content(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as temporary_directory:
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

    def test_cli_rejects_output_inside_task_to_keep_retries_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
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


if __name__ == "__main__":
    unittest.main()
