# Harbor task conversion contract

Apply this contract independently to every source task. A conversion is a
faithful packaging operation, not a new benchmark design.

## Map the source before writing files

Record these facts from the checked-out benchmark:

- stable source task ID and the source file or record that defines it;
- exact instruction presented to the agent;
- initial files, fixtures, services, environment variables, working directory,
  system packages, and language dependencies available during the task;
- agent-produced state that the source grader reads;
- grader entrypoint, its inputs, pass/fail or score calculation, and any grader
  dependencies;
- source-defined reward names and numeric ranges.

If the grader or required runtime state cannot be located or is ambiguous, stop
for that task. Do not infer expected answers from benchmark samples and do not
replace missing grading logic with `exit 0`, `exit 1`, a prose rubric, or an LLM
judge that the source benchmark did not use.

## Required task contents

The task directory must be self-contained and must include:

- `task.toml` with a stable task name and a valid agent environment definition;
- non-empty Harbor instructions (`instruction.md`, or the instruction files
  required by declared Harbor steps);
- `environment/` plus its complete build/runtime context, unless `task.toml`
  declares the agent environment image supported by Harbor;
- all task-owned inputs and fixtures at paths reproduced by the environment;
- a Harbor verifier entrypoint under `tests/`, plus all code and fixtures it
  imports or executes;
- verifier environment configuration/build context when the verifier is
  intentionally separate from the agent environment;
- artifact declarations when the agent and verifier exchange declared files
  across environments.

No task file may be a symlink, device, socket, or named pipe. No file may refer
to an absolute host path, the developer checkout, an undeclared network
resource, or another converted task directory. Preserve executable permissions
on every entrypoint. Empty directories are not represented by an EvalManifest;
create any required runtime directory from the task's build or setup logic.

Treat every source-controlled Dockerfile, Compose file, setup hook, and verifier
as untrusted code. Before a smoke run, reject privileged containers, host
network/PID/IPC modes, device mappings, Docker-socket access, and bind sources
outside the task directory. Run only in a disposable coding-agent or remote
sandbox with no developer credentials, host mounts, or unrelated files. Never
build or execute the benchmark directly on the developer host. Deny network
access by default. When the original benchmark requires network access,
allowlist only documented public hostnames and always deny loopback, link-local
and cloud-metadata addresses, RFC 1918/private ranges, and sandbox control-plane
endpoints.

Manifest serialization requires a POSIX environment with descriptor-relative
`O_NOFOLLOW` and `O_DIRECTORY` support. Route Windows checkouts through a
disposable Linux coding-agent sandbox; a path-based traversal is not an
acceptable fallback because it reintroduces symlink-swap races.

## Verifier behavior

The verifier must exercise the original grader semantics against final task
state. It must write every source-defined reward as a finite JSON number to the
Harbor reward output location expected by the selected Harbor task format. It
must fail clearly when required output is absent or malformed. Do not award a
constant result and do not silently turn grader errors into a score.

Wrapping a source grader is preferred to rewriting it. Any wrapper must only
adapt paths, invocation, and reward serialization; it must not change scoring.

## Completion gate

A task is ready to publish only when all of these are true:

1. Every mapped source input, dependency, service, and grader file exists in the
   task directory or is declared by its Harbor environment configuration.
2. `scripts/build_manifest.py` succeeds and its path list matches the complete
   task directory.
3. NeoSigma `validate_harbor_task` accepts that exact generated manifest.
4. In a disposable credential-free sandbox, Harbor constructs the environment,
   the agent can read task-only state, and the native verifier grades the final
   state and emits finite numeric rewards.
5. The same manifest bytes and stable idempotency key—not a regenerated variant—
   are supplied to `publish_harbor_task`.

Typed validation is mandatory but is not evidence of step 4. If step 4 cannot
be run safely, leave that task unpublished and state the blocker.
