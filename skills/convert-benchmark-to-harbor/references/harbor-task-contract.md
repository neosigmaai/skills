# Harbor task conversion contract

Apply this contract independently to every source task. It describes Harbor
**0.20.0**, the version the NeoSigma service uses to parse and run tasks. If
`harbor --version` reports another version, install 0.20.0 for checks; do not
rely on fields or behavior from a newer release.

## Format authority

`harbor init <org>/<name> --task` produces a starting point with example
content. It is not the full schema. The authority is the pinned parser:
`harbor/models/task/config.py` (`TaskConfig`) and `harbor/models/task/paths.py`
in the installed package. `check_task.py` runs that parser and adds the checks
Harbor does not make.

Harbor 0.20.0 ignores keys it does not know, so an invented field parses and
silently does nothing. Never add a field that is not in `TaskConfig`; never
invent a value for `network_mode` (`"no-network"`, `"public"`, `"allowlist"`).

Source behavior and the Harbor fields that carry it:

| Source behavior | Harbor 0.20.0 |
| --- | --- |
| Instruction | `instruction.md`; per step `steps/<name>/instruction.md` |
| Build recipe | `environment/Dockerfile` |
| Prebuilt image | `[environment].docker_image` (keep at least one source-derived file, such as `environment/IMAGE.md` recording the reference, because the `environment/` directory must exist and a manifest cannot hold an empty directory) |
| Sidecar services | `environment/docker-compose.yaml` (service `main` is the agent container) |
| Readiness | `[environment.healthcheck]`, `[steps.healthcheck]` |
| Working directory, user | `[environment].workdir`, `[agent].user`, `[verifier].user` |
| Resources and timeouts | `cpus`, `memory_mb`, `storage_mb`, `gpus`, `gpu_types`, `tpu`, `build_timeout_sec`, `[agent].timeout_sec`, `[verifier].timeout_sec` |
| Task MCP servers | `[[environment.mcp_servers]]` with `name`, `transport` (`stdio`, `sse`, `streamable-http`), `command`, `args`, `url`. There is no `cwd` or `env` field |
| Task skills | `[environment].skills_dir`: a directory inside the image |
| Environment variables | `[environment].env`, `[verifier].env`, `[solution.env]`; values are literals or `${NAME}` / `${NAME:-default}` references |
| Grader in its own container | `[verifier].environment_mode = "separate"` and optionally `[verifier.environment]`; its build files go in `tests/` (or `steps/<name>/tests/`) |
| Files passed from agent to grader | `artifacts` (task or step), `[[verifier.collect]]` |
| Several turns | `[[steps]]`, `min_reward`, `multi_step_reward_strategy` (`mean` or `final`) |
| Reference solution | `solution/solve.sh`, or `steps/<name>/solution/solve.sh` |

Harbor uploads `tests/` to `/tests` only after the agent finishes, and
`solution/` to `/solution` only for the oracle agent. `environment/` and
`instruction.md` are visible to the agent.

A separate verifier works differently in Harbor 0.20.0:

- Harbor does not upload `tests/` into it. Its image, built from
  `tests/Dockerfile` (build context `tests/`), must copy the grader files to
  `/tests`, for example `COPY . /tests/`.
- Without `[verifier.environment]` it inherits `[environment]`, including
  `workdir`. If that directory does not exist in the verifier image, the grader
  never starts and every trial reports a missing reward. Declare
  `[verifier.environment]` with a `workdir` that exists in the verifier image
  and an explicit `network_mode`.
- Artifacts are restored at their original source path (for example
  `/app/report.json`). An artifact the agent did not create is skipped, so the
  grader must handle its absence the way the source does.

## Required task contents

The task directory must be self-contained:

- `task.toml` with a stable `[task].name`, an explicit
  `[environment].network_mode`, and only `TaskConfig` fields;
- non-empty instructions (`instruction.md`, or one per declared step);
- `environment/` with the complete build context, or a `docker_image` plus a
  source-derived file;
- every task-owned input and fixture, at the paths the environment uses;
- a verifier entrypoint (`tests/test.sh` or a step's `tests/test.sh`) and every
  file it imports or runs;
- the separate verifier build definition, when the grader runs separately.

No file may refer to an absolute host path, the developer checkout, an
undeclared network resource, or another task directory.

## Reward contract

Harbor reads `/logs/verifier/reward.json` when it exists, and otherwise
`/logs/verifier/reward.txt`. The exit status of `test.sh` is not used.

- `reward.txt`: one finite number, stored under the name `reward`. Use it only
  when the source has exactly one unnamed score.
- `reward.json`: a JSON object mapping each source reward name to a finite
  number, for example `{"accuracy": 0.73}`. A bare number is rejected.
- Keep the source names, ranges and meanings. Do not collapse a continuous or
  named score into pass/fail, and do not average unrelated metrics.
- Harbor 0.20.0 accepts `NaN`, infinity, booleans and numeric strings. The
  wrapper must reject them: write no reward file instead.
- A legitimate zero is a score. A grader that cannot grade is not: when the
  source grader fails in the way the source treats as an error, write no
  reward file, so Harbor reports `RewardFileNotFoundError`.
- Delete both reward files before grading starts. In a single-step task with a
  shared verifier, Harbor does not clear `/logs/verifier`, so a stale file or a
  file the agent wrote would otherwise be read as the score.
- Write the reward file only after the grader finished, through a temporary
  file and a rename. `scripts/write_reward.py` does this and validates values.

A typical wrapper:

```sh
#!/bin/sh
set -eu
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/<source-grader> <source arguments> > /logs/verifier/grader-result.json
python3 /tests/write_reward.py --output /logs/verifier/reward.json \
  --keys <source reward names> < /logs/verifier/grader-result.json
```

If the source grader signals "graded, failed" with a nonzero exit status (for
example pytest exit 1), map that to the source score instead of letting
`set -e` turn it into a missing reward. Map only the signals the source
defines.

For multi-step tasks, each step writes its own rewards. `min_reward` reproduces
a source stop rule; `multi_step_reward_strategy` reproduces how the source
combines turns. If the source combines turns in another way, record a
`capability` blocker. Dataset aggregation (mean, pass@k, weighting, repeats,
seeds) is not a task field: record it in the task's conversion record.

## Graders that call models

Keep the grader's requested model and API protocol unchanged, and declare its
credential as a reference such as `OPENAI_API_KEY = "${OPENAI_API_KEY}"` in
`[verifier].env`. NeoSigma selects grader models through its catalog and
records any substitution; do not rewrite the grader, change its model, or add a
fallback in task files.

## Network and credential boundaries

Keep what the source needs and protect everything else:

- Set `network_mode` from the source: `no-network` when the source runs
  offline, `allowlist` with the documented public hosts when it needs them,
  `public` only when the source needs open web access. Phase overrides in
  `[agent]` and `[verifier]` apply only to that phase.
- Task-local services are part of the task. Compose sidecars, a database in
  the task container, and a stdio or local HTTP MCP server reached over the
  loopback or Compose network are allowed and must keep working. Do not remove
  or rewrite them to pass a smoke test.
- Host and control-plane resources are not part of the task. A task must not
  reach the developer host, cloud metadata (`169.254.169.254`), the Docker
  socket, or sandbox control endpoints. `check_task.py` rejects Compose
  services with `privileged`, host `network_mode`/`pid`/`ipc`/`uts`/`userns_mode`,
  `devices`, `cap_add`, the Docker socket, or bind sources outside the task.
- Builds may need network access to fetch the source's pinned dependencies;
  that is separate from the agent and verifier phases.
- Name credentials, never store them. Every credential is a `${NAME}`
  reference in the env map of the phase that consumes it: `[environment].env`
  for the agent environment and task services, `[verifier].env` for the
  grader. Keep the source's variable names. NeoSigma binds only credentials the
  customer selected for the run; a task cannot select a vault or grant itself
  access. An unbound required reference fails before the run.

Enforcement belongs to the runtime. Do not claim a network restriction is
enforced unless the runtime documents it; report a requirement the runtime
cannot meet as a `capability` blocker.

## What a manifest can represent

A manifest holds regular files only, each with its bytes and one executable
flag. NeoSigma recreates files with mode 0755 or 0644. These service defaults
apply (the service is authoritative):

| Limit | Value |
| --- | --- |
| Files | 1,000 |
| Bytes per file | 4 MiB |
| Total bytes | 20 MiB |
| Path depth | 16 components |
| Path length | 512 characters, canonical relative POSIX, no `\` or control characters |
| Directory entries scanned | 2,000 |

`build_manifest.py` reports, in one run, every entry it cannot represent:
`symlink`, `special_file` (FIFO, socket, device), `empty_directory`,
`unrepresentable_mode` (setuid, setgid, sticky), `invalid_path`,
`file_too_large`, `total_too_large`. It never skips or flattens an entry.

When a source needs something a manifest cannot hold, use a source-faithful
build step: create directories, links and permissions in the Dockerfile, or
fetch a large asset at build time from an immutable URL with a checksum the
source publishes, or use the source's image by digest. Record each external
asset in the conversion record. If no faithful representation exists, record a
`format` blocker.

Task bytes are immutable once published. External downloads and image tags
are not: a tag such as `python:3.12-slim` can change. Keep the source's
references unchanged and record whether each is pinned.

Serialization needs a POSIX system with descriptor-relative `O_NOFOLLOW` and
`O_DIRECTORY`. On Windows, use a disposable Linux sandbox; never replace the
serializer with a path-based copy.
