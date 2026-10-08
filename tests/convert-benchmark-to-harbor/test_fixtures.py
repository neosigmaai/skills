"""Validate the representative conversion fixtures with the pinned Harbor parser.

Run with the Harbor version NeoSigma pins:

    uv run --no-project --with harbor==0.20.0 \
        python -m unittest discover -s tests/convert-benchmark-to-harbor

These tests check parsing, serialization and conversion records. They do not
build images or run agents; sandboxed execution is recorded separately in each
fixture's record.json.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import harbor  # noqa: F401
except ImportError as error:  # pragma: no cover - depends on the test environment
    raise unittest.SkipTest(
        "install harbor==0.20.0 to run the parser fixture tests"
    ) from error


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "convert-benchmark-to-harbor" / "scripts"
FIXTURES = Path(__file__).parent / "fixtures"
TASK_FIXTURES = sorted(path.parent.name for path in FIXTURES.glob("*/task"))
# Faithful tasks whose sandbox run exposed a runtime limit; they stay unpublished.
BLOCKED_TASK_FIXTURES = {
    "compose-service": "compose_service_names_unresolvable_with_egress_control"
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BUILD_MANIFEST = _load("build_manifest")
MATERIALIZE = _load("materialize_manifest")
CHECK_TASK = _load("check_task")
CHECK_RECORD = _load("check_record")


def _record(fixture: str) -> dict:
    return json.loads((FIXTURES / fixture / "record.json").read_text())


def _codes(result: dict) -> list[str]:
    return [item["code"] for item in result["findings"]]


class FixtureTaskTests(unittest.TestCase):
    def test_fixture_tasks_pass_the_pinned_parser(self) -> None:
        self.assertGreaterEqual(len(TASK_FIXTURES), 6)
        for fixture in TASK_FIXTURES:
            with self.subTest(fixture=fixture):
                result = CHECK_TASK.check_task(
                    FIXTURES / fixture / "task",
                    harbor_version=CHECK_TASK.PLATFORM_HARBOR_VERSION,
                )
                self.assertEqual(result["findings"], [])
                self.assertEqual(result["harbor_version"], "0.20.0")

    def test_exact_manifest_bytes_materialize_to_a_task_that_still_parses(
        self,
    ) -> None:
        for fixture in TASK_FIXTURES:
            with (
                self.subTest(fixture=fixture),
                tempfile.TemporaryDirectory(dir="/private/tmp") as temporary,
            ):
                record = _record(fixture)
                manifest = BUILD_MANIFEST.build_manifest(
                    FIXTURES / fixture / "task", record["source"]["task_id"]
                )
                MATERIALIZE.validate_manifest(manifest)
                output = Path(temporary) / "task"
                MATERIALIZE.materialize(manifest, output)
                result = CHECK_TASK.check_task(output, harbor_version="0.20.0")
                self.assertEqual(result["findings"], [])
                rebuilt = BUILD_MANIFEST.build_manifest(
                    output, record["source"]["task_id"]
                )
                self.assertEqual(
                    BUILD_MANIFEST.manifest_digest(rebuilt),
                    BUILD_MANIFEST.manifest_digest(manifest),
                )

    def test_records_bind_the_current_fixture_bytes(self) -> None:
        for fixture in TASK_FIXTURES:
            with self.subTest(fixture=fixture):
                record = _record(fixture)
                manifest = BUILD_MANIFEST.build_manifest(
                    FIXTURES / fixture / "task", record["source"]["task_id"]
                )
                digest = BUILD_MANIFEST.manifest_digest(manifest)
                result = CHECK_RECORD.check_record(record, manifest_digest=digest)
                self.assertEqual(result["problems"], [])
                self.assertEqual(record["validation"]["manifest_digest"], digest)
                self.assertEqual(
                    result["publishable"], fixture not in BLOCKED_TASK_FIXTURES
                )

    def test_runtime_limits_block_a_faithful_task_instead_of_changing_it(
        self,
    ) -> None:
        for fixture, code in BLOCKED_TASK_FIXTURES.items():
            with self.subTest(fixture=fixture):
                blockers = _record(fixture)["blockers"]
                self.assertEqual(
                    [(item["category"], item["code"]) for item in blockers],
                    [("capability", code)],
                )

    def test_unsupported_and_ungradable_sources_stay_unpublished(self) -> None:
        expected = {
            "browser-cua": "capability",
            "missing-grader": "fidelity",
        }
        for fixture, category in expected.items():
            with self.subTest(fixture=fixture):
                record = _record(fixture)
                result = CHECK_RECORD.check_record(record)
                self.assertEqual(result["problems"], [])
                self.assertFalse(result["publishable"])
                self.assertIn(
                    category, [item["category"] for item in record["blockers"]]
                )
                self.assertFalse((FIXTURES / fixture / "task").exists())

    def test_task_copies_of_the_reward_writer_are_unchanged(self) -> None:
        reference = (SCRIPTS / "write_reward.py").read_bytes()
        copies = sorted(FIXTURES.glob("*/task/**/write_reward.py"))
        self.assertGreaterEqual(len(copies), 6)
        for path in copies:
            with self.subTest(path=path.relative_to(FIXTURES)):
                self.assertEqual(path.read_bytes(), reference)

    def test_source_graders_are_copied_without_changes(self) -> None:
        pairs = [
            (
                "coding-pinned-deps/source/tasks/legacy-version-001/grade.py",
                "coding-pinned-deps/task/tests/grade.py",
            ),
            ("qa-answer-file/source/eval.py", "qa-answer-file/task/tests/eval.py"),
            ("task-local-mcp/source/grade.py", "task-local-mcp/task/tests/grade.py"),
            ("compose-service/source/grade.py", "compose-service/task/tests/grade.py"),
            (
                "separate-verifier-artifacts/source/score.py",
                "separate-verifier-artifacts/task/tests/score.py",
            ),
            (
                "multi-step-named-rewards/source/check.py",
                "multi-step-named-rewards/task/steps/create/tests/check.py",
            ),
        ]
        for source, converted in pairs:
            with self.subTest(source=source):
                self.assertEqual(
                    (FIXTURES / source).read_bytes(),
                    (FIXTURES / converted).read_bytes(),
                )


class CheckTaskRejectionTests(unittest.TestCase):
    def _variant(self, fixture: str) -> Path:
        directory = Path(tempfile.mkdtemp(dir="/private/tmp"))
        self.addCleanup(shutil.rmtree, directory)
        task = directory / "task"
        shutil.copytree(FIXTURES / fixture / "task", task, symlinks=True)
        return task

    def _check(self, task: Path) -> dict:
        return CHECK_TASK.check_task(task, harbor_version="0.20.0")

    def test_unknown_task_toml_fields_are_reported(self) -> None:
        task = self._variant("coding-pinned-deps")
        config = task / "task.toml"
        config.write_text(
            config.read_text()
            + "\n[environment.sandbox]\nkind = 'x'\n"
            + "[[environment.mcp_servers]]\nname = 'a'\ntransport = 'stdio'\n"
            + "command = 'a'\ncwd = '/x'\n"
        )

        findings = self._check(task)["findings"]

        self.assertEqual(
            sorted(
                item["field"] for item in findings if item["code"] == "unknown_field"
            ),
            ["environment.mcp_servers[0].cwd", "environment.sandbox"],
        )

    def test_invalid_field_values_report_the_field(self) -> None:
        task = self._variant("coding-pinned-deps")
        config = task / "task.toml"
        config.write_text(config.read_text().replace('"no-network"', '"offline"'))

        findings = self._check(task)["findings"]

        self.assertEqual(
            [(item["code"], item["field"]) for item in findings],
            [("invalid_field", "environment.network_mode")],
        )

    def test_network_mode_must_come_from_the_source(self) -> None:
        task = self._variant("qa-answer-file")
        config = task / "task.toml"
        config.write_text(
            config.read_text().replace('network_mode = "no-network"\n', "")
        )

        self.assertIn("network_mode_implicit", _codes(self._check(task)))

    def test_compose_host_access_is_rejected(self) -> None:
        task = self._variant("compose-service")
        compose = task / "environment" / "docker-compose.yaml"
        compose.write_text(
            compose.read_text()
            + "    privileged: true\n"
            + "    network_mode: host\n"
            + "    volumes:\n"
            + "      - /var/run/docker.sock:/var/run/docker.sock\n"
            + "      - /etc:/host-etc:ro\n"
        )

        codes = _codes(self._check(task))

        for code in (
            "compose_privileged",
            "compose_host_namespace",
            "compose_docker_socket",
            "compose_host_bind",
        ):
            self.assertIn(code, codes)

    def test_literal_credentials_are_rejected_and_references_are_listed(self) -> None:
        task = self._variant("qa-answer-file")
        config = task / "task.toml"
        config.write_text(
            config.read_text().replace(
                "[verifier]\ntimeout_sec = 60.0\n",
                "[verifier]\ntimeout_sec = 60.0\n\n[verifier.env]\n"
                'OPENAI_API_KEY = "${OPENAI_API_KEY}"\nJUDGE_TOKEN = "abc123"\n',
            )
        )

        result = self._check(task)

        self.assertEqual(_codes(result), ["literal_secret_value"])
        self.assertEqual(
            result["requirements"]["credentials"],
            [
                {
                    "consumer": "verifier",
                    "field": "verifier.env.OPENAI_API_KEY",
                    "required": True,
                    "variable": "OPENAI_API_KEY",
                }
            ],
        )

    def test_image_only_task_still_needs_an_environment_file(self) -> None:
        task = self._variant("qa-answer-file")
        shutil.rmtree(task / "environment")
        config = task / "task.toml"
        config.write_text(
            config.read_text().replace(
                "[environment]\n",
                '[environment]\ndocker_image = "python:3.12-slim"\n',
            )
        )

        self.assertEqual(_codes(self._check(task)), ["missing_environment_directory"])

        (task / "environment").mkdir()
        (task / "environment" / "IMAGE.md").write_text("python:3.12-slim\n")
        self.assertEqual(self._check(task)["findings"], [])

    def test_placeholder_verifiers_are_fidelity_errors(self) -> None:
        task = self._variant("qa-answer-file")
        (task / "tests" / "test.sh").write_text("#!/bin/sh\nexit 0\n")

        findings = self._check(task)["findings"]

        self.assertEqual(
            [(item["code"], item["category"]) for item in findings],
            [("placeholder_verifier", "fidelity")],
        )

    def test_capabilities_are_listed_for_platform_comparison(self) -> None:
        requirements = {
            fixture: self._check(FIXTURES / fixture / "task")["requirements"]
            for fixture in TASK_FIXTURES
        }

        self.assertTrue(requirements["compose-service"]["compose"])
        self.assertEqual(
            requirements["task-local-mcp"]["mcp_servers"][0]["command"], "python3"
        )
        self.assertTrue(
            requirements["separate-verifier-artifacts"]["separate_verifier"]
        )
        self.assertEqual(requirements["separate-verifier-artifacts"]["artifacts"], 1)
        self.assertEqual(
            requirements["multi-step-named-rewards"]["steps"], ["create", "extend"]
        )


if __name__ == "__main__":
    unittest.main()
