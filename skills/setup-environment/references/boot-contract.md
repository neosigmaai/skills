# Setup script boot contract

This file is the contract for a NeoSigma workspace setup script, shown to users
as `neosigma-setup.sh`. It describes the machine the script boots on and every
rule the script must follow to pass verification. It does not describe how you
author the script; the skill that loaded this file covers that.

Read [`eval-harness-contract.md`](eval-harness-contract.md) for the eval entry
point the script must expose, and [`examples.md`](examples.md) for complete
scripts in the proven shape.

## What verification does

NeoSigma boots the saved script once in a fresh Linux sandbox and passes it
only when all of these hold:

1. The script exits 0 within the boot time limit.
2. The eval adapter answers its health check and its execution probe (see
   [`eval-harness-contract.md`](eval-harness-contract.md)).

After a clean boot, NeoSigma may also run one smoke command from the project.
A failed smoke command is reported, but it does not fail the boot.

The sandbox is fresh on every boot. A fix must be in the script itself.

## The boot machine

- The script runs as **root**, with Docker available. Write `docker`,
  `docker compose`, and `apt-get` steps inside the script.
- The primary repository is checked out at `/home/user/workspace/repo`. The
  script also receives it as `REPO_PATH`; fall back to
  `/home/user/workspace/repo` when `REPO_PATH` is unset.
- Every other repository of the workspace is checked out at
  `/home/user/workspace/<owner>/<name>/`, for example
  `/home/user/workspace/acme/web/`. Use these paths exactly. Do not guess other
  locations.
- Each checkout is already at the exact commit under test. Do not switch
  branches, reset `HEAD`, or pull newer source.
- Workspace environment variables are exported into the script's shell. The
  script never sees a value that is not stored in NeoSigma.
- The sandbox has a memory limit. A very large install or build can exceed it.
  The verification then reports a `resource` failure, which a script edit
  usually cannot fix.

## Never in a setup script

- **Never clone, fetch, pull, or check out a repository.** NeoSigma checks out
  every repository before the script starts. Repository network operations
  break reproducibility and bypass the commit under test.
- **Never migrate, seed, backfill, or write to a hosted or customer
  datastore.** `prisma migrate`, `drizzle-kit migrate`, `alembic upgrade`,
  `rails db:migrate`, and equivalent commands are allowed only against a local,
  disposable datastore that the script itself started and whose connection URL
  the script set itself. Never run one against an inherited `DATABASE_URL`. A
  service may use a hosted datastore at runtime only when its normal liveness
  check needs no write.
- **Never keep the script in the foreground.** Start services in the
  background, health-gate them, and exit 0. No `exec tail -F`, no bare `wait`,
  no infinite loop. A script that never exits fails as a timeout.
- **Never echo or hardcode a secret.** Read secrets from the environment.
- **Never use `pkill`, `pgrep`, `killall`, or a process-name match.** Stop only
  process groups the script recorded itself (see Idempotency).

## Start only the harness dependency closure

A workspace can own several repositories, but the eval harness rarely needs
every development service. Find the real harness entry point and its
transitive runtime dependencies, then install, build, and start only that
closure. Do not start a UI, realtime server, admin app, worker, datastore, or
sibling repository only because it exists or appears in a full-stack developer
launcher.

- `cd` into each required repository for its own install, build, and start
  steps. Reference sibling paths explicitly.
- Start required shared infrastructure first, then required services in
  dependency order. Health-gate each one before its consumer starts.
- If removing a service does not change the harness path, it does not belong in
  the script.

## Users and permissions

- `/home/user` and `/home/user/workspace` are mode 700 and owned by `user`
  (uid 1000). A service that runs as another system account, such as
  `postgres`, cannot enter them. Keep that service's data, log, socket, and
  config paths in locations such as `/var/lib/<svc>` or `/tmp/<svc>`, and chown
  them to the service account.
- The script runs as root, but the eval harness runs as `user`. Every runtime,
  interpreter, binary, cache, or symlink the script creates must be executable
  and traversable by `user`. For Python, keep the provided
  `UV_PYTHON_INSTALL_DIR` (never point it under `/root`) and run
  `uv sync --locked` as `user`. Before the script reports the adapter ready,
  prove the harness command runs as `user`.
- The boot may start in `/root` or another directory `user` cannot enter, and
  `runuser` does not change the working directory. Every command that drops to
  `user` must `cd` into a directory `user` can enter, including import and
  readiness checks:

      runuser -u user -- env HOME=/home/user bash -lc "cd '$REPO' && uv sync --locked"

  Setting `HOME` alone is not enough. Never loosen permissions on a root-owned
  directory or file to make a check pass.

## Script shape

- The first two lines are `#!/usr/bin/env bash` and `set -euo pipefail`.
- Define and use these helpers. Verification shows only `[eval-stack]` lines,
  so an unprefixed milestone makes a failed boot impossible to diagnose.
  Prefixed lines carry static text and non-secret paths only.

      log() { echo "[eval-stack] $1"; }
      die() { echo "[eval-stack] ERROR: $1" >&2; exit 1; }

- Install dependencies with the lockfile command for the ecosystem:
  `uv sync --locked`, `npm ci`, `pnpm install --frozen-lockfile`,
  `bun install --frozen-lockfile`, `poetry install`, `go mod download`. Use the
  plain install only when no lockfile exists.

## Idempotency

The script must be safe to run again on a half-provisioned machine.

- Use `mkdir -p`, `docker start || docker run`, and create-if-missing guards
  for infrastructure.
- Start each long-running service in its own process group with `setsid`,
  capture `$!` immediately, and write the leader PID, the process group ID, and
  the start-time token from `/proc/<pid>/stat` to a service-specific root-owned
  file under `/run/neosigma/`.
- On a rerun, signal a recorded group only when all three live values still
  match. Remove a stale, malformed, or reused record without signaling. Send
  `TERM`, wait a bounded time, then `KILL` the same verified group.
  [`examples.md`](examples.md) has a `stop_group` helper that does this.

## Services

- Start every required service in the background, in its recorded process
  group, bound to all interfaces with the framework's own flag (`--host
  0.0.0.0`, `--bind`, `--hostname`), and redirect its output to a log file:

      setsid <command> --host 0.0.0.0 >/tmp/<name>.log 2>&1 < /dev/null &

  The eval adapter is the one exception: it binds only to loopback.
- **Gate on the Docker daemon before the first `docker` command.** Docker
  starts at the same time as the script, so the first `docker run` can fail
  with `Cannot connect to the Docker daemon`:

      for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
      docker info >/dev/null 2>&1 || die "Docker daemon never became ready"

- **Start local datastores before the services that need them, and wait until
  they answer.** Prefer `docker run --network=host`, because bridge networking
  inside the sandbox is unreliable. Block on `pg_isready`, `redis-cli ping`, or
  `SELECT 1` in a bounded retry loop before you start a consumer.
- **Health-gate every service before the script exits.** Poll its liveness
  endpoint (such as `/health`) in a bounded retry loop and fail the script when
  it never answers. Gate on the endpoint that is up once the process listens.
  Do not gate on full end-to-end behavior, because many services start lazily
  for optional dependencies.
- **Per-service health lines are parsed.** For each service, use exactly these
  two shapes, with the same stable name (letters, digits, `.`, `_`, `-`) in
  both:

      log "<name> healthy on :<port>"
      die "<name> did not become healthy on :<port> (see /tmp/<name>.log)"

  A line without `on :<port>`, such as `log "all services healthy"`, is not
  parsed as a service.

## Runtime log declaration

When the script starts any long-running service, write one root-owned JSON file
at `/run/neosigma/runtime-logs.json` after the health gates pass
(`mkdir -p /run/neosigma`, mode `0644`). It names where each service's output
goes, so NeoSigma can follow the logs after the script exits:

    {
      "version": 1,
      "services": [
        {"name": "api", "stream": "combined", "port": 8000,
         "health_endpoint": "/health",
         "source": {"kind": "file", "path": "/tmp/api.log"}},
        {"name": "eval-adapter", "stream": "combined",
         "source": {"kind": "file", "path": "/tmp/eval-adapter.log"}}
      ]
    }

- `name` matches `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
- A `file` source `path` is the exact absolute path the service writes to,
  under `/tmp/`, `/var/log/`, `/home/user/workspace/`, or `/run/neosigma/`. No
  wildcards, directories, or `..`.
- For Docker Compose services use
  `{"kind": "compose", "project_directory": "<abs dir>", "services": ["api"]}`.
  For a plain container use `{"kind": "container", "container": "app-redis"}`.
- There is no command field. Never put `tail -f` or any other command in the
  file.
- Declare at most 8 services, and only services the script started.

## Environment variables

- **Guard every variable the boot requires.** Before anything reads it, fail
  with exactly this marker, which NeoSigma parses to tell the user which
  variable to add:

      require_env() { [ -n "${!1:-}" ] || die "missing-env var=$1"; }
      require_env DATABASE_URL

  Guard only variables the boot really requires. A skipped integration's key
  is not guarded.
- **Normalize JSON-typed settings.** Settings libraries such as
  pydantic-settings decode a `list[str]` or `dict` variable as JSON before any
  validator runs. A bare or comma-separated value such as
  `ALLOWED_ORIGINS=http://a,http://b` crashes the service at startup. Wrap such
  a value into a JSON array before you export it, and leave a value that
  already starts with `[` unchanged. The failure looks like
  `json.decoder.JSONDecodeError` or `error parsing value for field`.

## Hosted datastores

The sandbox runs outside the user's network. A hosted URL works only when the
host is publicly reachable. Private addresses (`localhost`, `10.x`,
`172.16-31.x`, `192.168.x`, `*.internal`) can never be reached, and a host that
allowlists IP addresses must allow the sandbox egress address.

Before any service that needs a hosted datastore starts, prove the endpoint is
reachable. Use this helper verbatim. Its parsing handles URLs without a port,
query strings without a path, `[IPv6]:port`, and credentials in the URL, and
its failure marker is parsed by NeoSigma:

    # $1 = env var NAME holding a URL (value never echoed)
    # $2 = default port when the URL omits one
    # $3 = "$RUN" for an endpoint that allowlists IP addresses (else omit)
    check_reachable() {
      local url="${!1:-}" hp host port lower
      if [ -z "$url" ]; then return 0; fi
      hp="${url#*://}"; hp="${hp#*@}"; hp="${hp%%[/?#]*}"
      case "$hp" in
        \[*\]*) host="${hp#\[}"; host="${host%%\]*}"; port="${hp##*\]:}" ;;
        *)      host="${hp%%:*}"; port="${hp##*:}" ;;
      esac
      case "$port" in ''|*[!0-9]*) port="$2" ;; esac
      lower="$(printf '%s' "$host" | tr 'A-Z' 'a-z')"
      case "$lower" in
        localhost|*.local|*.internal|0.0.0.0|127.*|10.*|192.168.*|\
        172.1[6-9].*|172.2[0-9].*|172.3[01].*|169.254.*|::1|fd*:*|fc*:*)
          die "unreachable-datastore var=$1 host=$host port=$port reason=private-address";;
      esac
      timeout 5 ${3:-} bash -c 'exec 3<>/dev/tcp/"$1"/"$2"' _ "$host" "$port" 2>/dev/null \
        || die "unreachable-datastore var=$1 host=$host port=$port reason=connect-failed"
      log "$1 endpoint reachable ($host:$port)"
    }
    check_reachable DATABASE_URL 5432
    check_reachable REDIS_URL 6379

Check only hosted endpoints the user supplied. Local containers the script
starts are covered by their own readiness gates.

### IP-allowlisted endpoints

Some workspaces route outbound connections through a static egress address.
When that is available, NeoSigma sets `NEOSIGMA_PROXY_IP` in the boot. For each
endpoint that allowlists IP addresses, define this once next to the helpers:

    RUN="${NEOSIGMA_PROXY_IP:+proxychains4}"

Then prefix every command that connects to that endpoint with `$RUN`,
including its `check_reachable` call (pass `"$RUN"` as the third argument) and
the background services that use it (`setsid $RUN <command> ... &`). Without
`NEOSIGMA_PROXY_IP` the prefix is empty and the connection is direct. In that
case, log `[eval-stack] static IP proxy not available; connecting directly to
<service>` once. The proxy intercepts dynamically linked programs only, so a
statically linked binary (typical for Go) is not proxied. Never prefix
commands for endpoints that do not allowlist IP addresses, and never reference
`NEOSIGMA_PROXY_*` outside the `${VAR:+...}` form.
