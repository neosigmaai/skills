from __future__ import annotations

import base64
import copy
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parent


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
SUMMARIZE = _load("summarize_trials")
CHECK_RECORD = _load("check_record")


def _file(path: str, content: bytes, *, executable: bool = False) -> dict:
    return {
        "path": path,
        "content": base64.b64encode(content).decode(),
        "executable": executable,
        "media_type": "application/octet-stream",
    }


class MaterializeManifestTests(unittest.TestCase):
    def test_materialized_tree_reserializes_to_the_same_digest(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            task = temporary / "task"
            (task / "tests").mkdir(parents=True)
            (task / "task.toml").write_text("[task]\n")
            verifier = task / "tests" / "test.sh"
            verifier.write_text("#!/bin/sh\n")
            verifier.chmod(0o700)
            manifest_path = temporary / "manifest.json"
            manifest = BUILD_MANIFEST.build_manifest(task, "source/1")
            manifest_path.write_text(json.dumps(manifest))
            digest = BUILD_MANIFEST.manifest_digest(manifest)
            output = temporary / "materialized"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "materialize_manifest.py"),
                    str(manifest_path),
                    str(output),
                    "--expect-digest",
                    digest,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["manifest_digest"], digest)
            rebuilt = BUILD_MANIFEST.build_manifest(output, "source/1")
            self.assertEqual(BUILD_MANIFEST.manifest_digest(rebuilt), digest)
            mode = stat.S_IMODE((output / "tests" / "test.sh").stat().st_mode)
            self.assertEqual(mode, 0o755)
            self.assertEqual(stat.S_IMODE((output / "task.toml").stat().st_mode), 0o644)

    def test_materialize_rejects_a_digest_mismatch_without_writing(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            temporary = Path(temporary_directory)
            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "source_task_id": "s",
                        "files": [_file("a.txt", b"a")],
                    }
                )
            )
            output = temporary / "materialized"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "materialize_manifest.py"),
                    str(manifest_path),
                    str(output),
                    "--expect-digest",
                    "sha256:" + "0" * 64,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("error: digest_mismatch:", result.stderr)
            self.assertFalse(output.exists())

    def test_validate_manifest_rejects_service_invalid_manifests(self) -> None:
        cases = {
            "duplicate_path": [_file("a", b"1"), _file("a", b"2")],
            "path_collision": [_file("a", b"1"), _file("a/b", b"2")],
            "invalid_path": [_file("../escape", b"1")],
        }
        for code, files in cases.items():
            with self.subTest(code=code):
                manifest = {"version": 1, "source_task_id": "s", "files": files}
                with self.assertRaises(MATERIALIZE.ManifestError) as raised:
                    MATERIALIZE.validate_manifest(manifest)
                self.assertIn(code, [item.code for item in raised.exception.problems])


class WriteRewardTests(unittest.TestCase):
    def _run(self, payload: str, output: str, *keys: str):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary_directory:
            path = Path(temporary_directory) / output
            arguments = [
                sys.executable,
                str(SCRIPTS / "write_reward.py"),
                "--output",
                str(path),
            ]
            if keys:
                arguments += ["--keys", ",".join(keys)]
            result = subprocess.run(
                arguments, input=payload, check=False, capture_output=True, text=True
            )
            content = path.read_text() if path.exists() else None
            leftovers = [item.name for item in path.parent.iterdir() if item != path]
        return result, content, leftovers

    def test_writes_selected_named_rewards_including_a_legitimate_zero(self) -> None:
        result, content, leftovers = self._run(
            '{"accuracy": 0, "f1": 0.5, "diagnostic": "ignored"}',
            "reward.json",
            "accuracy",
            "f1",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(content), {"accuracy": 0, "f1": 0.5})
        self.assertEqual(leftovers, [])

    def test_reward_txt_holds_one_finite_number(self) -> None:
        result, content, _ = self._run('{"resolved": 1}', "reward.txt")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(content, "1\n")

    def test_invalid_results_write_no_reward_file(self) -> None:
        cases = [
            ("NaN", ()),
            ('{"score": NaN}', ()),
            ('{"score": Infinity}', ()),
            ('{"score": 1e999}', ()),
            ('{"score": true}', ()),
            ('{"score": "0.5"}', ()),
            ("{}", ()),
            ("0.73", ()),
            ('{"other": 1}', ("score",)),
            ("not json", ()),
        ]
        for payload, keys in cases:
            with self.subTest(payload=payload):
                result, content, leftovers = self._run(payload, "reward.json", *keys)
                self.assertEqual(result.returncode, 2)
                self.assertIsNone(content)
                self.assertEqual(leftovers, [])

    def test_reward_txt_rejects_several_values(self) -> None:
        result, content, _ = self._run('{"a": 1, "b": 0}', "reward.txt")

        self.assertEqual(result.returncode, 2)
        self.assertIsNone(content)


class SummarizeTrialsTests(unittest.TestCase):
    def test_classifies_scores_and_failures_separately(self) -> None:
        def trial(rewards=None, exception=None):
            return {
                "task_name": "t",
                "trial_name": "x",
                "agent_info": {"name": "oracle"},
                "verifier_result": None if rewards is None else {"rewards": rewards},
                "exception_info": None
                if exception is None
                else {"exception_type": exception},
            }

        cases = [
            (trial({"accuracy": 0}), "graded"),
            (trial({"accuracy": 0.5}, "AgentTimeoutError"), "graded"),
            (trial({"accuracy": float("nan")}), "reward_nonfinite"),
            (trial({"accuracy": True}), "reward_not_numeric"),
            (trial({}), "reward_empty"),
            (trial(None, "RewardFileNotFoundError"), "reward_file_missing"),
            (trial(None, "RewardFileEmptyError"), "reward_file_empty"),
            (trial(None, "VerifierOutputParseError"), "reward_unparseable"),
            (trial(None, "VerifierTimeoutError"), "verifier_timeout"),
            (trial(None, "AgentTimeoutError"), "agent_error_not_graded"),
            (trial(None, "SomethingElse"), "execution_error"),
        ]
        for document, outcome in cases:
            with self.subTest(outcome=outcome):
                self.assertEqual(SUMMARIZE.classify(document)["outcome"], outcome)

    def test_a_failed_step_grader_is_not_hidden_by_the_trial_result(self) -> None:
        document = {
            "task_name": "t",
            "trial_name": "x",
            "agent_info": {"name": "nop"},
            "verifier_result": None,
            "exception_info": None,
            "step_results": [
                {
                    "step_name": "create",
                    "verifier_result": None,
                    "exception_info": {"exception_type": "RewardFileNotFoundError"},
                }
            ],
        }

        summary = SUMMARIZE.classify(document)

        self.assertEqual(summary["outcome"], "reward_file_missing")
        self.assertEqual(summary["exception_type"], "RewardFileNotFoundError")

    def test_expectations_compare_outcomes_and_rewards(self) -> None:
        trials = [
            {
                "task_name": "t",
                "agent": "oracle",
                "outcome": "graded",
                "rewards": {"a": 1},
            },
            {
                "task_name": "t",
                "agent": "nop",
                "outcome": "graded",
                "rewards": {"a": 1},
            },
        ]
        expected = {
            "t/oracle": {"outcome": "graded", "rewards": {"a": 1}},
            "t/nop": {"outcome": "graded", "rewards": {"a": 0}},
            "t/broken": {"outcome": "reward_file_missing"},
        }

        problems = SUMMARIZE.mismatches(trials, expected)

        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("t/nop: rewards"))
        self.assertEqual(problems[1], "t/broken: no trial found")


READY_RECORD = {
    "record_version": 1,
    "skill_bundle_digest": "sha256:" + "1" * 64,
    "status": "ready",
    "source": {
        "repository": "https://example.com/bench.git",
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "dataset": "bench",
        "split": "test",
        "task_id": "task-1",
        "definition": "tasks/task-1/task.json",
    },
    "mapping": {
        "instruction": {"source": "tasks/task-1/prompt.md"},
        "inputs": [],
        "interaction": ["files"],
        "environment": {
            "definition": "Dockerfile",
            "base_images": [],
            "dependencies": [],
            "services": [],
            "user": None,
            "workdir": "/app",
            "network": "no-network",
            "resources": {},
            "timeouts": {},
        },
        "tools": [],
        "credentials": [
            {"variable": "OPENAI_API_KEY", "consumers": ["verifier"], "required": True}
        ],
        "grader": {
            "source": "grade.py",
            "invocation": "python grade.py",
            "reads": ["/app/out.txt"],
            "dependencies": [],
            "model": None,
            "rewards": [{"name": "accuracy", "range": [0, 1]}],
            "failure_signal": "nonzero exit",
            "aggregation": "mean",
            "termination": "single turn",
        },
        "external_assets": [],
    },
    "validation": {
        "manifest_digest": "sha256:" + "a" * 64,
        "local_check": {"ok": True, "harbor_version": "0.20.0"},
        "service_validation": {"digest": "sha256:" + "a" * 64},
        "smoke": {
            "materialized_digest": "sha256:" + "a" * 64,
            "controls": [
                {
                    "name": "passing",
                    "outcome": "graded",
                    "rewards": {"accuracy": 1},
                    "source_rewards": {"accuracy": 1},
                },
                {
                    "name": "failing",
                    "outcome": "graded",
                    "rewards": {"accuracy": 0},
                    "source_rewards": {"accuracy": 0},
                },
                {"name": "broken_grader", "outcome": "reward_file_missing"},
            ],
        },
        "fidelity_protocol": "exact",
    },
    "blockers": [],
}


class CheckRecordTests(unittest.TestCase):
    def test_complete_ready_record_is_publishable_for_its_manifest(self) -> None:
        result = CHECK_RECORD.check_record(
            READY_RECORD, manifest_digest="sha256:" + "a" * 64
        )

        self.assertEqual(result["problems"], [])
        self.assertTrue(result["publishable"])
        self.assertEqual(
            result["idempotency_key"],
            CHECK_RECORD.idempotency_key(READY_RECORD["source"]),
        )

    def test_changed_manifest_bytes_are_not_publishable(self) -> None:
        result = CHECK_RECORD.check_record(
            READY_RECORD, manifest_digest="sha256:" + "b" * 64
        )

        self.assertFalse(result["publishable"])
        self.assertEqual([item["field"] for item in result["problems"]], ["--manifest"])

    def test_idempotency_key_changes_with_source_revision_only(self) -> None:
        changed = dict(READY_RECORD["source"], revision="f" * 40)

        self.assertNotEqual(
            CHECK_RECORD.idempotency_key(READY_RECORD["source"]),
            CHECK_RECORD.idempotency_key(changed),
        )
        self.assertLessEqual(len(CHECK_RECORD.idempotency_key(changed)), 200)

    def test_evidence_problems_block_publication(self) -> None:
        def mutate(change):
            record = copy.deepcopy(READY_RECORD)
            change(record)
            return [
                item["code"] for item in CHECK_RECORD.check_record(record)["problems"]
            ]

        controls = lambda record: record["validation"]["smoke"]["controls"]  # noqa: E731
        cases = {
            "broken_grader_scored": lambda r: controls(r)[2].update(outcome="graded"),
            "reward_mismatch": lambda r: controls(r)[0].update(rewards={"accuracy": 0}),
            "missing_control": lambda r: controls(r).pop(1),
            "digest_mismatch": lambda r: r["validation"]["smoke"].update(
                materialized_digest="sha256:" + "c" * 64
            ),
            "local_check_missing": lambda r: r["validation"]["local_check"].update(
                harbor_version="0.24.0"
            ),
            "credential_value_forbidden": lambda r: r["mapping"]["credentials"][
                0
            ].update(value="sk-secret"),
            "open_blockers": lambda r: r["blockers"].append(
                {
                    "category": "capability",
                    "code": "x",
                    "detail": "d",
                    "recommendation": "r",
                }
            ),
            "missing_field": lambda r: r["mapping"]["grader"].pop("aggregation"),
        }
        for code, change in cases.items():
            with self.subTest(code=code):
                self.assertIn(code, mutate(change))

    def test_blocked_record_needs_a_categorized_blocker(self) -> None:
        record = copy.deepcopy(READY_RECORD)
        record["status"] = "blocked"
        del record["validation"]

        self.assertIn(
            "missing_blocker",
            [item["code"] for item in CHECK_RECORD.check_record(record)["problems"]],
        )


if __name__ == "__main__":
    unittest.main()
