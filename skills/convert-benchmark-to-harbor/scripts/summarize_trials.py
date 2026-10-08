#!/usr/bin/env python3
"""Classify Harbor trial results and compare them with expected control outcomes.

A legitimate zero reward is "graded". A missing, empty, malformed or non-finite
reward, and a grader or environment failure, each get their own outcome code so
they are never counted as a score. Outcome codes come from Harbor's exception
type names and the reward values, never from exception messages.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

EXCEPTION_OUTCOMES = {
    "RewardFileNotFoundError": "reward_file_missing",
    "RewardFileEmptyError": "reward_file_empty",
    "VerifierOutputParseError": "reward_unparseable",
    "ValidationError": "reward_unparseable",
    "VerifierTimeoutError": "verifier_timeout",
    "AddTestsDirError": "verifier_error",
    "DownloadVerifierDirError": "verifier_error",
}
AGENT_EXCEPTIONS = {"AgentTimeoutError", "NonZeroAgentExitCodeError"}
GRADED = "graded"
RESULT_FILE = "result.json"
MISMATCH_EXIT_CODE = 3


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs_directory", type=Path)
    parser.add_argument(
        "--expected",
        type=Path,
        help='JSON object keyed by "<task_name>/<agent>" or job name: '
        '{"outcome": ..., "rewards": {...}, "tolerance": 0}',
    )
    return parser.parse_args()


def reward_outcome(rewards: object) -> str:
    """Return graded only for a non-empty object of finite numbers."""
    if not isinstance(rewards, dict) or not rewards:
        return "reward_empty"
    for value in rewards.values():
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "reward_not_numeric"
        if not math.isfinite(value):
            return "reward_nonfinite"
    return GRADED


def _phase_outcome(result: dict[str, object]) -> tuple[str, str | None]:
    exception = result.get("exception_info") or {}
    exception_type = (
        exception.get("exception_type") if isinstance(exception, dict) else None
    )
    verifier = result.get("verifier_result")
    if isinstance(verifier, dict) and verifier.get("rewards") is not None:
        return reward_outcome(verifier["rewards"]), exception_type
    if exception_type is None:
        return "not_graded", None
    if exception_type in AGENT_EXCEPTIONS:
        return "agent_error_not_graded", exception_type
    return EXCEPTION_OUTCOMES.get(exception_type, "execution_error"), exception_type


def classify(result: dict[str, object]) -> dict[str, object]:
    """Summarize one Harbor TrialResult document."""
    outcome, exception_type = _phase_outcome(result)
    verifier = result.get("verifier_result")
    agent = result.get("agent_info")
    steps = [
        {
            "step_name": step.get("step_name"),
            "outcome": _phase_outcome(step)[0],
            "exception_type": _phase_outcome(step)[1],
            "rewards": (step.get("verifier_result") or {}).get("rewards"),
        }
        for step in result.get("step_results") or []
        if isinstance(step, dict)
    ]
    failed_step = next((step for step in steps if step["outcome"] != GRADED), None)
    if outcome == "not_graded" and failed_step is not None:
        outcome, exception_type = failed_step["outcome"], failed_step["exception_type"]
    return {
        "task_name": result.get("task_name"),
        "trial_name": result.get("trial_name"),
        "agent": agent.get("name") if isinstance(agent, dict) else None,
        "outcome": outcome,
        "exception_type": exception_type,
        "rewards": verifier.get("rewards") if isinstance(verifier, dict) else None,
        "steps": steps,
    }


def load_trials(jobs_directory: Path) -> list[dict[str, object]]:
    """Return every trial result below a Harbor jobs directory."""
    trials = []
    for path in sorted(jobs_directory.rglob(RESULT_FILE)):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "trial_name" in document:
            trials.append({**classify(document), "job": path.parent.parent.name})
    return trials


def _rewards_match(
    actual: object, expected: dict[str, float], tolerance: float
) -> bool:
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    return all(
        abs(float(actual[name]) - float(value)) <= tolerance
        for name, value in expected.items()
    )


def mismatches(
    trials: list[dict[str, object]], expected: dict[str, dict[str, object]]
) -> list[str]:
    """Return one line per expectation that the trials do not meet."""
    problems = []
    for key, expectation in expected.items():
        matching = [
            trial
            for trial in trials
            if key in (trial.get("job"), f"{trial['task_name']}/{trial['agent']}")
        ]
        if not matching:
            problems.append(f"{key}: no trial found")
        for trial in matching:
            outcome, wanted = trial["outcome"], expectation["outcome"]
            if outcome != wanted:
                problems.append(f"{key}: outcome {outcome}, expected {wanted}")
            elif "rewards" in expectation and not _rewards_match(
                trial["rewards"],
                expectation["rewards"],  # type: ignore[arg-type]
                float(expectation.get("tolerance", 0)),  # type: ignore[arg-type]
            ):
                problems.append(
                    f"{key}: rewards {trial['rewards']}, "
                    f"expected {expectation['rewards']}"
                )
    return problems


def main() -> int:
    """Print the trial summary; exit 3 when expectations are not met."""
    arguments = _arguments()
    trials = load_trials(arguments.jobs_directory)
    report: dict[str, object] = {"trials": trials}
    if arguments.expected is not None:
        expected = json.loads(arguments.expected.read_text(encoding="utf-8"))
        report["mismatches"] = mismatches(trials, expected)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return MISMATCH_EXIT_CODE if report.get("mismatches") else 0


if __name__ == "__main__":
    raise SystemExit(main())
