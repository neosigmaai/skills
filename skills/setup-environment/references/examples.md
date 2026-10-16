# Setup script examples

Two complete scripts in the proven shape. Both follow
[`boot-contract.md`](boot-contract.md): helpers first, idempotent restart by
recorded process group, readiness gates before consumers, services bound to
`0.0.0.0`, a per-service health gate, and a clean `exit 0`.

They show the service stages only. A real script also starts the eval adapter
from [`eval-harness-contract.md`](eval-harness-contract.md) and writes
`/run/neosigma/runtime-logs.json`. Verification fails without the adapter.

## Two HTTP services and a local datastore

A backend repo ships two HTTP services that boot lazily for their DB / SaaS
deps but need Redis up at boot, plus one list-typed settings var. The user
picked a hosted DB (`DATABASE_URL` provided) and local Redis. This is the
canonical script shape. Note the hosted-datastore reachability preflight, the
readiness gate before the services, the JSON normalization of the list var,
services bound to `0.0.0.0`, a per-service `/health` gate, and a clean `exit 0`
(no foreground tail):

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="${REPO_PATH:-/home/user/workspace/repo}"; cd "$REPO"
log() { echo "[eval-stack] $1"; }
die() { echo "[eval-stack] ERROR: $1" >&2; exit 1; }
RUN="${NEOSIGMA_PROXY_IP:+proxychains4}"   # user said the DB allowlists IPs

# Idempotent restart: stop only process groups this script previously recorded.
stop_group() {
  local file="$1" pid pgid start extra live_pgid live_start i
  [ -f "$file" ] || return 0
  read -r pid pgid start extra < "$file" || true
  [ -z "${extra:-}" ] || { rm -f "$file"; return 0; }
  case "$pid" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  case "$pgid" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  case "$start" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  live_pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  live_start="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}' || true)"
  [ "$live_pgid:$live_start" = "$pgid:$start" ] || { rm -f "$file"; return 0; }
  kill -TERM -- "-$pgid" 2>/dev/null || true
  for i in $(seq 1 20); do kill -0 -- "-$pgid" 2>/dev/null || break; sleep 0.25; done
  kill -KILL -- "-$pgid" 2>/dev/null || true
  rm -f "$file"
}
mkdir -p /run/neosigma
stop_group /run/neosigma/app-agents.pgid
stop_group /run/neosigma/app-realtime.pgid

# Hosted datastore: prove it is reachable BEFORE anything depends on it, so an
# unreachable endpoint fails in seconds with a parsed marker, not as a
# health-gate timeout that reads as a script bug.
# $3="$RUN" because this endpoint IP-allowlists: probe through the static IP.
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
check_reachable DATABASE_URL 5432 "$RUN"

# Docker starts with the script: wait for the daemon before the first docker command.
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker info >/dev/null 2>&1 || die "Docker daemon never became ready"

# Datastore first, with a readiness gate (host networking; bridge networking is flaky).
docker ps --format '{{.Names}}' | grep -qx app-redis \
  || { docker rm -f app-redis 2>/dev/null || true; \
       docker run -d --name app-redis --network=host redis:7-alpine >/dev/null; }
for i in $(seq 1 20); do
  docker exec app-redis redis-cli ping 2>/dev/null | grep -q PONG && { log "redis up"; break; }
  sleep 0.5; [ "$i" = 20 ] && die "redis did not answer PING"
done

log "installing deps (locked)"; runuser -u user -- env HOME=/home/user bash -lc "cd '$REPO' && uv sync --locked"

# list[str] settings var: the settings layer JSON-decodes it before validators,
# so a bare/comma value must become a JSON array or the service crashes at boot.
case "${WS_ALLOWED_ORIGINS:-}" in
  \[*) : ;;                                       # already JSON: leave it
  "")  export WS_ALLOWED_ORIGINS='["http://localhost:3000"]' ;;
  *)   export WS_ALLOWED_ORIGINS="$(python3 -c 'import json,os;print(json.dumps([s.strip() for s in os.environ["WS_ALLOWED_ORIGINS"].split(",") if s.strip()]))')" ;;
esac

log "starting services on :8000 and :8001"
setsid uv run uvicorn app_agents.api.main:app --host 0.0.0.0 --port 8000 >/tmp/agents.log 2>&1 < /dev/null &
pid=$!; pgid="$(ps -o pgid= -p "$pid" | tr -d ' ' || true)"; start="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}' || true)"
[ "$pgid" = "$pid" ] && [ -n "$start" ] || die "agents service exited before PID capture"
printf '%s %s %s\n' "$pid" "$pgid" "$start" > /run/neosigma/app-agents.pgid
setsid uv run uvicorn app_realtime.api.main:app --host 0.0.0.0 --port 8001 >/tmp/realtime.log 2>&1 < /dev/null &
pid=$!; pgid="$(ps -o pgid= -p "$pid" | tr -d ' ' || true)"; start="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}' || true)"
[ "$pgid" = "$pid" ] && [ -n "$start" ] || die "realtime service exited before PID capture"
printf '%s %s %s\n' "$pid" "$pgid" "$start" > /run/neosigma/app-realtime.pgid

# Health-gate on the liveness endpoint (up once the process listens; the
# services boot lazily for DB/sandbox so /health does NOT require them).
for svc in "8000 agents" "8001 realtime"; do
  set -- $svc
  for i in $(seq 1 40); do
    curl -fs -m3 "http://localhost:$1/health" >/dev/null 2>&1 && { log "$2 healthy on :$1"; break; }
    sleep 1; [ "$i" = 40 ] && die "$2 did not become healthy on :$1 (see /tmp/$2.log)"
  done
done

log "all services healthy"
exit 0   # do NOT `exec tail -F` / `wait`: verification boots this once.
```

The service on port 8000 reads `DATABASE_URL` lazily, so `/health` passes
without a database query. The reachability preflight proves the endpoint
answers, and the health gate stays on the service's own liveness.

## A multi-repository dependency closure

A two-repo project whose real harness enters through a frontend in sibling repo
`acme/web` and calls a backend in the **primary** repo. Both repos are in the
harness dependency closure, so the single script installs/builds both, starts
the backend, then the frontend, and wires the cross-repo URL explicitly. If the
harness called the backend directly, the frontend would be omitted. Note each
step `cd`s into the repo it operates on; never assume a single tree:

```bash
#!/usr/bin/env bash
set -euo pipefail
PRIMARY="${REPO_PATH:-/home/user/workspace/repo}"
WEB="/home/user/workspace/acme/web"   # sibling repo, owner-qualified path
log() { echo "[eval-stack] $1"; }
die() { echo "[eval-stack] ERROR: $1" >&2; exit 1; }

# Idempotent restart by recorded process group.
stop_group() {
  local file="$1" pid pgid start extra live_pgid live_start i
  [ -f "$file" ] || return 0
  read -r pid pgid start extra < "$file" || true
  [ -z "${extra:-}" ] || { rm -f "$file"; return 0; }
  case "$pid" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  case "$pgid" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  case "$start" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  live_pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  live_start="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}' || true)"
  [ "$live_pgid:$live_start" = "$pgid:$start" ] || { rm -f "$file"; return 0; }
  kill -TERM -- "-$pgid" 2>/dev/null || true
  for i in $(seq 1 20); do kill -0 -- "-$pgid" 2>/dev/null || break; sleep 0.25; done
  kill -KILL -- "-$pgid" 2>/dev/null || true
  rm -f "$file"
}
mkdir -p /run/neosigma
stop_group /run/neosigma/acme-backend.pgid
stop_group /run/neosigma/acme-web.pgid

# 1) Shared infra first, once the Docker daemon answers.
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
docker info >/dev/null 2>&1 || die "Docker daemon never became ready"

docker ps --format '{{.Names}}' | grep -qx app-redis \
  || { docker rm -f app-redis 2>/dev/null || true; \
       docker run -d --name app-redis --network=host redis:7-alpine >/dev/null; }
for i in $(seq 1 20); do
  docker exec app-redis redis-cli ping 2>/dev/null | grep -q PONG && { log "redis up"; break; }
  sleep 0.5; [ "$i" = 20 ] && die "redis did not answer PING"
done

# 2) Backend (primary repo): install + start, then health-gate before the
#    frontend that depends on it.
cd "$PRIMARY"
log "installing backend deps"; runuser -u user -- env HOME=/home/user bash -lc "cd '$PRIMARY' && uv sync --locked"
log "starting backend on :8000"
setsid uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 >/tmp/backend.log 2>&1 < /dev/null &
pid=$!; pgid="$(ps -o pgid= -p "$pid" | tr -d ' ' || true)"; start="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}' || true)"
[ "$pgid" = "$pid" ] && [ -n "$start" ] || die "backend exited before PID capture"
printf '%s %s %s\n' "$pid" "$pgid" "$start" > /run/neosigma/acme-backend.pgid
for i in $(seq 1 40); do
  curl -fs -m3 "http://localhost:8000/health" >/dev/null 2>&1 && { log "backend healthy on :8000"; break; }
  sleep 1; [ "$i" = 40 ] && die "backend did not become healthy on :8000 (see /tmp/backend.log)"
done

# 3) Frontend (sibling repo): install + start, pointed at the backend it needs.
cd "$WEB"
log "installing frontend deps"; npm ci
export NEXT_PUBLIC_API_URL="http://localhost:8000"   # cross-repo wiring
log "starting frontend on :3000"
setsid npm run start -- --hostname 0.0.0.0 --port 3000 >/tmp/web.log 2>&1 < /dev/null &
pid=$!; pgid="$(ps -o pgid= -p "$pid" | tr -d ' ' || true)"; start="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}' || true)"
[ "$pgid" = "$pid" ] && [ -n "$start" ] || die "frontend exited before PID capture"
printf '%s %s %s\n' "$pid" "$pgid" "$start" > /run/neosigma/acme-web.pgid
for i in $(seq 1 40); do
  curl -fs -m3 "http://localhost:3000" >/dev/null 2>&1 && { log "frontend healthy on :3000"; break; }
  sleep 1; [ "$i" = 40 ] && die "frontend did not become healthy on :3000 (see /tmp/web.log)"
done

log "harness dependency closure healthy"
exit 0   # the required harness services are ready; do NOT tail/wait.
```

The shape is identical to the single-repo example (readiness gates, `0.0.0.0`
binds, per-service `/health`, clean `exit 0`). The only difference is that it `cd`s
into each **required** repo and wires the cross-repo URL. Scale this to the
discovered dependency closure the same way: required infrastructure, then required services
in dependency order, gating health between tiers.
