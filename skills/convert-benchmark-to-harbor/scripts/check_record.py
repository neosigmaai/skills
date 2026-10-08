#!/usr/bin/env python3
"""Check one task's conversion record and decide whether its manifest may be published.

The record is the source-derived mapping and the evidence for one converted
task (see references/conversion-record.md). A task may be published only when
its record is complete, has no blockers, and binds the same manifest digest to
the local Harbor check, NeoSigma validation and the sandboxed smoke run of a
fresh materialization. The output also gives the publication idempotency key.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

RECORD_VERSION = 1
PLATFORM_HARBOR_VERSION = "0.20.0"
STATUSES = {"ready", "blocked"}
BLOCKER_CATEGORIES = {"format", "fidelity", "credential", "capability"}
INTERACTIONS = {
    "files",
    "final_answer_file",
    "final_message",
    "structured_response",
    "tools",
    "browser",
    "desktop",
    "multi_turn",
}
CREDENTIAL_CONSUMERS = {"build", "agent", "task_service", "verifier"}
CREDENTIAL_KEYS = {"variable", "consumers", "required", "purpose"}
CONTROLS = {"passing", "failing", "broken_grader"}
GRADED = "graded"
SOURCE_FIELDS = ("repository", "revision", "dataset", "split", "task_id", "definition")
MAPPING_FIELDS = {
    "instruction": dict,
    "inputs": list,
    "interaction": list,
    "environment": dict,
    "tools": list,
    "credentials": list,
    "grader": dict,
    "external_assets": list,
}
ENVIRONMENT_FIELDS = (
    "definition",
    "base_images",
    "dependencies",
    "services",
    "user",
    "workdir",
    "network",
    "resources",
    "timeouts",
)
GRADER_FIELDS = (
    "source",
    "invocation",
    "reads",
    "dependencies",
    "model",
    "rewards",
    "failure_signal",
    "aggregation",
    "termination",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="The manifest to publish; its digest must match the record.",
    )
    return parser.parse_args()


def idempotency_key(source: dict[str, object]) -> str:
    """Return the retry key for one source task at one source revision.

    A retry of the same manifest returns the same task. Different bytes under
    the same key are rejected by NeoSigma with idempotency_key_reused.
    """
    identity = f"{source['repository']}@{source['revision']}#{source['task_id']}"
    return "harbor:" + hashlib.sha256(identity.encode()).hexdigest()


class Problems:
    """Collected record problems with stable codes."""

    def __init__(self) -> None:
        """Start with no problems."""
        self.items: list[dict[str, str]] = []

    def add(self, code: str, field: str, message: str) -> None:
        """Record one problem."""
        self.items.append({"code": code, "field": field, "message": message})


def _require(
    document: dict[str, object], name: str, kind: type, field: str, found: Problems
) -> object:
    value = document.get(name)
    if not isinstance(value, kind) or (kind is str and not value):
        found.add("missing_field", f"{field}{name}", f"{field}{name} is required")
        return None
    return value


def _check_source(record: dict[str, object], found: Problems) -> None:
    source = _require(record, "source", dict, "", found)
    if isinstance(source, dict):
        for name in SOURCE_FIELDS:
            _require(source, name, str, "source.", found)


def _check_credentials(credentials: list[object], found: Problems) -> None:
    for index, item in enumerate(credentials):
        field = f"mapping.credentials[{index}]"
        if not isinstance(item, dict) or set(item) - CREDENTIAL_KEYS:
            found.add(
                "credential_value_forbidden",
                field,
                "a credential entry may name a variable and its consumers only; "
                "never record a credential value",
            )
            continue
        consumers = item.get("consumers")
        if (
            not isinstance(consumers, list)
            or not consumers
            or set(consumers) - CREDENTIAL_CONSUMERS
        ):
            found.add(
                "invalid_field",
                f"{field}.consumers",
                f"consumers must be a subset of {sorted(CREDENTIAL_CONSUMERS)}",
            )


def _check_grader(
    grader: dict[str, object], found: Problems, *, require_rewards: bool
) -> None:
    for name in GRADER_FIELDS:
        if name not in grader:
            found.add(
                "missing_field", f"mapping.grader.{name}", f"grader.{name} is required"
            )
    rewards = grader.get("rewards")
    if not require_rewards:
        return
    if (
        not isinstance(rewards, list)
        or not rewards
        or not all(
            isinstance(item, dict) and item.get("name") and "range" in item
            for item in rewards
        )
    ):
        found.add(
            "missing_field",
            "mapping.grader.rewards",
            "list every source reward with its name and range",
        )


def _check_mapping(record: dict[str, object], found: Problems) -> None:
    """Require every source-derived mapping field; a blocked task may lack rewards."""
    mapping = _require(record, "mapping", dict, "", found)
    if not isinstance(mapping, dict):
        return
    for name, kind in MAPPING_FIELDS.items():
        _require(mapping, name, kind, "mapping.", found)
    unknown = set(mapping.get("interaction") or []) - INTERACTIONS
    if unknown:
        found.add(
            "invalid_field", "mapping.interaction", f"unknown modes {sorted(unknown)}"
        )
    environment = mapping.get("environment")
    if isinstance(environment, dict):
        for name in ENVIRONMENT_FIELDS:
            if name not in environment:
                found.add(
                    "missing_field",
                    f"mapping.environment.{name}",
                    f"environment.{name} is required",
                )
    if isinstance(mapping.get("credentials"), list):
        _check_credentials(mapping["credentials"], found)
    if isinstance(mapping.get("grader"), dict):
        _check_grader(
            mapping["grader"], found, require_rewards=record.get("status") == "ready"
        )


def _check_blockers(record: dict[str, object], found: Problems) -> list[object]:
    blockers = record.get("blockers")
    if not isinstance(blockers, list):
        found.add("missing_field", "blockers", "blockers must be a list")
        return []
    for index, blocker in enumerate(blockers):
        if (
            not isinstance(blocker, dict)
            or blocker.get("category") not in BLOCKER_CATEGORIES
            or not blocker.get("code")
            or not blocker.get("detail")
            or not blocker.get("recommendation")
        ):
            found.add(
                "invalid_blocker",
                f"blockers[{index}]",
                "a blocker needs a category, code, detail and recommendation",
            )
    return blockers


def _check_controls(
    smoke: dict[str, object],
    *,
    reward_names: set[str],
    exact: bool,
    found: Problems,
) -> None:
    controls = {
        item.get("name"): item
        for item in smoke.get("controls") or []
        if isinstance(item, dict)
    }
    required = {"failing", "broken_grader"}
    if not smoke.get("no_passing_state_reason"):
        required.add("passing")
    for name in sorted(required - set(controls)):
        found.add("missing_control", f"validation.smoke.controls.{name}", name)
    for name, control in controls.items():
        field = f"validation.smoke.controls.{name}"
        outcome = control.get("outcome")
        if name == "broken_grader":
            if outcome == GRADED:
                found.add(
                    "broken_grader_scored",
                    field,
                    "a broken grader produced a score; failures must not be scores",
                )
            continue
        if outcome != GRADED:
            found.add("control_not_graded", field, f"outcome is {outcome}")
            continue
        rewards = control.get("rewards") or {}
        if set(rewards) - reward_names:
            found.add("reward_name_changed", field, "rewards differ from source names")
        if exact and rewards != control.get("source_rewards"):
            found.add(
                "reward_mismatch",
                field,
                "converted rewards differ from the source grader on the same state",
            )


def _check_ready(
    record: dict[str, object], manifest_digest: str | None, found: Problems
) -> None:
    validation = _require(record, "validation", dict, "", found)
    if not isinstance(validation, dict):
        return
    digest = validation.get("manifest_digest")
    local = validation.get("local_check") or {}
    service = validation.get("service_validation") or {}
    smoke = validation.get("smoke") or {}
    if local.get("ok") is not True or local.get("harbor_version") != (
        PLATFORM_HARBOR_VERSION
    ):
        found.add(
            "local_check_missing",
            "validation.local_check",
            f"check_task.py must pass with Harbor {PLATFORM_HARBOR_VERSION}",
        )
    bound = {
        "validation.service_validation.digest": service.get("digest"),
        "validation.smoke.materialized_digest": smoke.get("materialized_digest"),
    }
    if manifest_digest is not None:
        bound["--manifest"] = manifest_digest
    for field, value in bound.items():
        if not digest or value != digest:
            found.add(
                "digest_mismatch",
                field,
                "every check must use the exact manifest that will be published",
            )
    mapping = record.get("mapping") or {}
    grader = mapping.get("grader") or {} if isinstance(mapping, dict) else {}
    names = {
        str(item.get("name"))
        for item in grader.get("rewards") or []
        if isinstance(item, dict)
    }
    exact = validation.get("fidelity_protocol") == "exact"
    if not exact and not validation.get("fidelity_protocol"):
        found.add(
            "missing_field",
            "validation.fidelity_protocol",
            "use exact, or describe the stochastic comparison",
        )
    _check_controls(smoke, reward_names=names, exact=exact, found=found)


def manifest_digest_of(path: Path) -> str:
    """Return the NeoSigma digest of a manifest file."""
    script = Path(__file__).with_name("build_manifest.py")
    spec = importlib.util.spec_from_file_location("build_manifest", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load build_manifest.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.manifest_digest(json.loads(path.read_bytes()))


def check_record(
    record: object, *, manifest_digest: str | None = None
) -> dict[str, object]:
    """Return the structured result of checking one conversion record."""
    found = Problems()
    if not isinstance(record, dict) or record.get("record_version") != RECORD_VERSION:
        found.add("invalid_record", "record_version", "record_version must be 1")
        return {"ok": False, "publishable": False, "problems": found.items}
    for name in ("skill_bundle_digest", "status"):
        _require(record, name, str, "", found)
    _check_source(record, found)
    _check_mapping(record, found)
    blockers = _check_blockers(record, found)
    status = record.get("status")
    if status not in STATUSES:
        found.add("invalid_field", "status", "status must be ready or blocked")
    elif status == "blocked" and not blockers:
        found.add("missing_blocker", "blockers", "a blocked task must say why")
    elif status == "ready":
        if blockers:
            found.add("open_blockers", "blockers", "a ready task cannot have blockers")
        _check_ready(record, manifest_digest, found)
    source = record.get("source")
    key = idempotency_key(source) if isinstance(source, dict) else None
    ok = not found.items
    return {
        "ok": ok,
        "publishable": ok and status == "ready",
        "status": status,
        "idempotency_key": key,
        "problems": found.items,
    }


def main() -> int:
    """Print the record check; exit 2 unless the record is complete and consistent.

    With --manifest, also exit 2 unless that exact manifest may be published.
    """
    arguments = _arguments()
    record = json.loads(arguments.record.read_text(encoding="utf-8"))
    digest = manifest_digest_of(arguments.manifest) if arguments.manifest else None
    result = check_record(record, manifest_digest=digest)
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not result["ok"] or (arguments.manifest and not result["publishable"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
