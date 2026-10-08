#!/usr/bin/env python3
"""Check one Harbor task directory with the pinned Harbor parser.

NeoSigma validation reports only that a task is not Harbor-compatible. This
check runs the same Harbor parser locally and reports the file, field and a
stable code for each problem, plus checks Harbor itself does not make:
unknown task.toml keys (Harbor ignores them), an implicit network mode, Compose
host access, and literal credential values. It also lists the credential
references and runtime capabilities the task requires, so they can be compared
with what the NeoSigma runtime supports. It never runs task code.

Run it with the Harbor version NeoSigma pins, for example:
    uv run --no-project --with harbor==0.20.0 python check_task.py <task-directory>
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
import typing
from pathlib import Path, PurePosixPath

import yaml
from harbor.environments.definition import require_agent_environment_definition
from harbor.models.task.config import TaskConfig
from harbor.models.task.task import Task
from harbor.models.task.verifier_mode import (
    resolve_effective_verifier_env_config,
    task_has_any_separate_verifier,
)
from pydantic import BaseModel, ValidationError

PLATFORM_HARBOR_VERSION = "0.20.0"
CONFIG_FILE = "task.toml"
COMPOSE_FILE = "docker-compose.yaml"
ENV_TEMPLATE = re.compile(r"^\$\{([^}:]+)(?::-(.*))?\}$")
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
DEPRECATED_KEYS = {
    ("environment", "allow_internet"),
    ("environment", "memory"),
    ("environment", "storage"),
    ("", "version"),
}
FREE_FORM_FIELDS = {"metadata", "env"}
HOST_NAMESPACE_KEYS = ("network_mode", "pid", "ipc", "userns_mode", "uts", "cgroup")
DOCKER_SOCKET = "docker.sock"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_directory", type=Path)
    parser.add_argument(
        "--harbor-version",
        default=PLATFORM_HARBOR_VERSION,
        help="Harbor version the NeoSigma service pins.",
    )
    return parser.parse_args()


class Findings:
    """Collected problems, each with a stable code and report category."""

    def __init__(self) -> None:
        """Start with no findings."""
        self.items: list[dict[str, str]] = []

    def add(
        self,
        code: str,
        message: str,
        *,
        category: str = "format",
        file: str = CONFIG_FILE,
        field: str = "",
        severity: str = "error",
    ) -> None:
        """Record one finding."""
        self.items.append(
            {
                "severity": severity,
                "category": category,
                "code": code,
                "file": file,
                "field": field,
                "message": message,
            }
        )

    @property
    def has_errors(self) -> bool:
        """Return whether any finding blocks publication."""
        return any(item["severity"] == "error" for item in self.items)


def _models_in(annotation: object) -> list[type[BaseModel]]:
    """Return every pydantic model class nested in a type annotation."""
    if isinstance(annotation, typing.ForwardRef):
        annotation = annotation.__forward_arg__
    if isinstance(annotation, str):
        annotation = getattr(sys.modules[TaskConfig.__module__], annotation, None)
    if typing.get_origin(annotation) is typing.Literal:
        return []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    models: list[type[BaseModel]] = []
    for argument in typing.get_args(annotation):
        models.extend(_models_in(argument))
    return models


def _unknown_keys(
    model: type[BaseModel], data: object, location: tuple[str, ...], found: Findings
) -> None:
    """Report keys Harbor would silently ignore."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        field = model.model_fields.get(key)
        if field is None:
            if (location[-1] if location else "", key) not in DEPRECATED_KEYS:
                found.add(
                    "unknown_field",
                    f"Harbor {_harbor_version()} has no field {key!r} here; "
                    "it would be ignored",
                    field=".".join((*location, key)),
                )
            continue
        if key in FREE_FORM_FIELDS:
            continue
        models = _models_in(field.annotation)
        if not models:
            continue
        items = value if isinstance(value, list) else [value]
        for index, item in enumerate(items):
            suffix = (f"{key}[{index}]",) if isinstance(value, list) else (key,)
            _unknown_keys(models[0], item, (*location, *suffix), found)


def _harbor_version() -> str:
    return importlib.metadata.version("harbor")


def _check_config(task_directory: Path, found: Findings) -> TaskConfig | None:
    config_path = task_directory / CONFIG_FILE
    if not config_path.is_file():
        found.add("missing_file", "task.toml is missing")
        return None
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        found.add("invalid_toml", str(error))
        return None
    _unknown_keys(TaskConfig, data, (), found)
    try:
        config = TaskConfig.model_validate(data)
    except ValidationError as error:
        for detail in error.errors():
            found.add(
                "invalid_field",
                detail["msg"],
                field=".".join(str(part) for part in detail["loc"]),
            )
        return None
    _check_network_explicit(data, found)
    return config


def _check_network_explicit(data: dict[str, object], found: Findings) -> None:
    """Harbor defaults to public network; the source must decide it."""
    environment = data.get("environment")
    if not isinstance(environment, dict) or "network_mode" not in environment:
        found.add(
            "network_mode_implicit",
            "set [environment].network_mode from the source; Harbor's default "
            "is public network access",
            field="environment.network_mode",
        )
    verifier = data.get("verifier")
    verifier_environment = (
        verifier.get("environment") if isinstance(verifier, dict) else None
    )
    if (
        isinstance(verifier_environment, dict)
        and "network_mode" not in verifier_environment
    ):
        found.add(
            "network_mode_implicit",
            "set [verifier.environment].network_mode from the source",
            field="verifier.environment.network_mode",
        )


def _check_structure(task_directory: Path, found: Findings) -> Task | None:
    """Mirror the NeoSigma service task-directory validation."""
    if not (task_directory / "environment").is_dir():
        found.add(
            "missing_environment_directory",
            "environment/ must exist and contain at least one file, also for "
            "image-only tasks",
            file="environment",
        )
        return None
    try:
        task = Task(task_directory)
        require_agent_environment_definition(
            task.paths.environment_dir,
            docker_image=task.config.environment.docker_image,
        )
    except (OSError, ValueError) as error:
        found.add("invalid_task", str(error), file="environment")
        return None
    for step in task.config.steps or [None]:
        environment = resolve_effective_verifier_env_config(task.config, step)
        if environment is None:
            continue
        tests = task.paths.tests_dir
        if step is not None and task.paths.step_tests_dir(step.name).is_dir():
            tests = task.paths.step_tests_dir(step.name)
        try:
            require_agent_environment_definition(
                tests, docker_image=environment.docker_image
            )
        except OSError as error:
            found.add(
                "missing_verifier_environment",
                str(error),
                file=tests.relative_to(task_directory).as_posix(),
            )
    _check_instructions_and_verifiers(task, found)
    return task


def _check_instructions_and_verifiers(task: Task, found: Findings) -> None:
    steps = task.config.steps or []
    try:
        instructions = (
            [task.step_instruction(step.name) for step in steps]
            if task.has_steps
            else [task.instruction]
        )
    except OSError as error:
        found.add("missing_instruction", str(error), file="instruction.md")
        return
    if any(not instruction.strip() for instruction in instructions):
        found.add("empty_instruction", "every instruction must be non-empty")
    names = [step.name for step in steps] or [None]
    for name in names:
        path = (
            task.paths.discovered_step_test_path_for(name, task.config.environment.os)
            if name
            else None
        ) or task.paths.discovered_test_path_for(task.config.environment.os)
        if path is None:
            found.add(
                "missing_verifier",
                f"no verifier entrypoint for {'step ' + name if name else 'task'}",
                file="tests",
            )
            continue
        commands = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if commands in (["exit 1"], ["false"], ["exit 0"], ["true"]):
            found.add(
                "placeholder_verifier",
                "the verifier is a constant placeholder, not the source grader",
                category="fidelity",
                file=path.relative_to(task.task_dir).as_posix(),
            )


def _env_maps(config: TaskConfig) -> list[tuple[str, str, dict[str, str]]]:
    """Return (field, consumer, env) for every map Harbor resolves from the host."""
    maps = [
        ("environment.env", "agent environment", config.environment.env),
        ("verifier.env", "verifier", config.verifier.env),
        ("solution.env", "reference solution", config.solution.env),
    ]
    if config.verifier.environment is not None:
        maps.append(
            (
                "verifier.environment.env",
                "verifier environment",
                config.verifier.environment.env,
            )
        )
    for index, step in enumerate(config.steps or []):
        maps.append((f"steps[{index}].verifier.env", "verifier", step.verifier.env))
    return maps


def _credentials(config: TaskConfig, found: Findings) -> list[dict[str, object]]:
    references: list[dict[str, object]] = []
    for field, consumer, env in _env_maps(config):
        for key, value in env.items():
            match = ENV_TEMPLATE.fullmatch(value)
            if match is not None:
                references.append(
                    {
                        "field": f"{field}.{key}",
                        "variable": match.group(1),
                        "consumer": consumer,
                        "required": match.group(2) is None,
                    }
                )
            elif SECRET_NAME.search(key) and value:
                found.add(
                    "literal_secret_value",
                    f"{key} has a literal value; reference it as ${{{key}}} and "
                    "never store a credential in task files",
                    category="credential",
                    field=f"{field}.{key}",
                )
    return references


def _compose_files(task_directory: Path) -> list[Path]:
    return sorted(
        path
        for path in task_directory.rglob(COMPOSE_FILE)
        if path.is_file() and not path.is_symlink()
    )


def _volume_source(volume: object) -> str | None:
    if isinstance(volume, str):
        parts = volume.split(":")
        return parts[0] if len(parts) > 1 else None
    if isinstance(volume, dict) and volume.get("type", "bind") == "bind":
        source = volume.get("source")
        return source if isinstance(source, str) else None
    return None


def _bind_problem(source: str, base: Path) -> tuple[str, str] | None:
    """Return why a bind mount reaches outside the task, if it does."""
    if DOCKER_SOCKET in source:
        return "compose_docker_socket", "the Docker socket must not be mounted"
    if source.startswith(("/", "~")) or ".." in PurePosixPath(source).parts:
        return (
            "compose_host_bind",
            f"bind source {source!r} is outside the task directory",
        )
    if not (base / source).exists():
        return "compose_missing_bind", f"bind source {source!r} does not exist"
    return None


def _check_compose_service(
    name: str, service: dict[str, object], base: Path, task: Path, found: Findings
) -> None:
    relative = base.relative_to(task).as_posix()
    file = f"{relative}/{COMPOSE_FILE}" if relative != "." else COMPOSE_FILE
    problems: list[tuple[str, str]] = []
    if service.get("privileged") is True:
        problems.append(("compose_privileged", "privileged containers are not allowed"))
    for key in HOST_NAMESPACE_KEYS:
        if service.get(key) == "host":
            problems.append(
                ("compose_host_namespace", f"{key}: host shares a host namespace")
            )
    for key in ("devices", "cap_add", "device_cgroup_rules"):
        if service.get(key):
            problems.append(
                ("compose_host_access", f"{key} grants host device or kernel access")
            )
    for volume in service.get("volumes") or []:
        source = _volume_source(volume)
        if source is not None and source.startswith((".", "/", "~")):
            problems.append(_bind_problem(source, base))
    for code, message in filter(None, problems):
        found.add(code, message, file=file, field=f"services.{name}")


def _check_compose(task_directory: Path, found: Findings) -> bool:
    files = _compose_files(task_directory)
    for path in files:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (UnicodeError, yaml.YAMLError) as error:
            found.add(
                "invalid_compose",
                str(error),
                file=path.relative_to(task_directory).as_posix(),
            )
            continue
        services = document.get("services") if isinstance(document, dict) else None
        for name, service in (services or {}).items():
            if isinstance(service, dict):
                _check_compose_service(
                    str(name), service, path.parent, task_directory, found
                )
    return bool(files)


def _requirements(
    config: TaskConfig, *, uses_compose: bool, credentials: list[dict[str, object]]
) -> dict[str, object]:
    """Summarize runtime capabilities to compare with NeoSigma platform support."""
    environment = config.environment
    return {
        "docker_image": environment.docker_image,
        "compose": uses_compose,
        "os": environment.os.value,
        "network_mode": environment.network_mode.value,
        "allowed_hosts": environment.allowed_hosts or [],
        "agent_network_mode": config.agent.network_mode.value
        if config.agent.network_mode
        else None,
        "verifier_network_mode": config.verifier.network_mode.value
        if config.verifier.network_mode
        else None,
        "resources": {
            "cpus": environment.cpus,
            "memory_mb": environment.memory_mb,
            "storage_mb": environment.storage_mb,
            "gpus": environment.gpus,
            "tpu": environment.tpu.model_dump() if environment.tpu else None,
        },
        "mcp_servers": [server.model_dump() for server in environment.mcp_servers],
        "skills_dir": environment.skills_dir,
        "workdir": environment.workdir,
        "healthcheck": environment.healthcheck is not None,
        "separate_verifier": task_has_any_separate_verifier(config),
        "verifier_collect": len(config.verifier.collect),
        "artifacts": len(config.artifacts)
        + sum(len(step.artifacts) for step in config.steps or []),
        "steps": [step.name for step in config.steps or []],
        "multi_step_reward_strategy": config.multi_step_reward_strategy.value
        if config.multi_step_reward_strategy
        else None,
        "credentials": credentials,
    }


def check_task(task_directory: Path, *, harbor_version: str) -> dict[str, object]:
    """Return the full structured check result for one task directory."""
    found = Findings()
    installed = _harbor_version()
    if installed != harbor_version:
        found.add(
            "harbor_version_mismatch",
            f"installed Harbor {installed}; NeoSigma pins {harbor_version}",
            file="",
        )
    config = _check_config(task_directory, found)
    requirements: dict[str, object] | None = None
    if config is not None:
        _check_structure(task_directory, found)
        uses_compose = _check_compose(task_directory, found)
        credentials = _credentials(config, found)
        requirements = _requirements(
            config, uses_compose=uses_compose, credentials=credentials
        )
    return {
        "ok": not found.has_errors,
        "harbor_version": installed,
        "task_directory": str(task_directory),
        "findings": found.items,
        "requirements": requirements,
    }


def main() -> int:
    """Print the check result as JSON; exit 2 when any error was found."""
    arguments = _arguments()
    result = check_task(
        arguments.task_directory, harbor_version=arguments.harbor_version
    )
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
