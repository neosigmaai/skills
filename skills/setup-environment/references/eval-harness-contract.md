# Eval harness contract

A setup script passes verification only when the booted stack exposes the
project's real agent harness to NeoSigma's evals. The script does this through
a NeoSigma-owned broker on loopback at `http://127.0.0.1:7391/neosigma/eval/v1`.
After the script exits, verification calls the broker's health check and runs
its execution probe. A failure is reported as `health_probe`.

Read [`boot-contract.md`](boot-contract.md) first. This file adds the harness
rules only.

## Find the real harness

Inspect the repository's API routes, CLI commands, request models, tests, and
local development scripts. Find the normal surface that runs the customer's
agent on one complete input and waits for its actual result. In a
multi-repository workspace, inspect the sibling repositories too.

- Do not ask the user for a per-eval command, an expected output, or a
  hand-written adapter.
- When several independent agent surfaces exist, ask the user once which
  logical agent this workspace represents.
- A queue, an asynchronous job, a run-status endpoint, or an endpoint that
  creates another sandbox is not an entry point. Those return scheduling
  state, not the agent's result.
- When the repository has no direct synchronous seam, the script calls `die`
  and the setup is not eval-ready. Never fabricate an adapter to pass.

## The entry point

First search the checkout for a repository-owned entry point that already
implements this contract. When one exists, use it directly in `argv`. Do not
copy, regenerate, or rewrite it.

Only when no compatible entry point exists may the script install one
generated entry point at `/usr/local/lib/neosigma-eval-adapter/entrypoint.py`,
root-owned and readable by `user`. Never generate it inside the checkout: an
untracked file changes the repository under test and can activate repository
hooks. Do not write another HTTP server, set a harness-command environment
variable, or store a shell command.

The entry point runs as `user`. It receives one complete EvalInput as JSON on
stdin and `NEOSIGMA_EVAL_SESSION_ID` in its environment.

- EvalInput has a non-empty `task` and optional `taskType`, `context`,
  `githubRepo`, `outputSchema`, and `messages`. It is not the application's
  request body. Translate it into the application's normal synchronous CLI or
  API call and wait for the real result.
- `messages` carries the earlier turns of a multi-turn eval as
  `[{"role":"user"|"assistant","content":"..."}]`, in order. It is absent on
  the first turn. When the application accepts conversation history, pass
  `messages` through, because the eval grades the whole conversation.
- Print exactly one JSON line to stdout:

      {"protocolVersion":1,"status":"ok","output":<the real output>}

  For a safe, expected failure, print:

      {"protocolVersion":1,"status":"error","failure":{"stage":"setup|input|dependency|service|execution","code":"lowercase_id"}}

- `output` is only the application's actual result (or the requested
  structured result), never an internal wrapper object.
- Never print raw stderr, secrets, application errors, a rubric, or an expected
  output. Never echo the input, synthesize an answer, or call a model in place
  of the system under test.
- Report every failure as `status: "error"`. Never print `status: "ok"` with
  placeholder output, because a broken harness then reads as a wrong answer.
- Give each failure class its own `code`, for example `import_failed`,
  `dependency_unavailable`, or `agent_run_failed`. Never map every exception to
  one code: the code is all the probe diagnostics show.
- A generated entry point loads the application when it starts, before it
  reads the input, so the script's self-test proves the application loads.

## The manifest

Write one root-owned regular file at `/run/neosigma/eval-harness.json`, mode
`0644`:

    {
      "cwd": "/home/user/workspace/repo",
      "argv": ["python3", "/home/user/workspace/repo/<real-entrypoint>.py"],
      "environment": ["NAME_ONLY_IF_NEEDED"],
      "probe": {"task": "one harmless real eval case"},
      "execution": {"mode": "one_shot", "continuationProbe": {...}}
    }

- `argv` is passed directly to exec, never through a shell.
- `cwd` stays under the primary repository.
- `environment` lists variable names only, never values. The entry point
  receives only the variables named here.
- `probe` is one harmless, non-secret EvalInput with a real `task`, using the
  same translation path as a real case.
- Do not add an adapter version or a shell-command field.
- `execution` is required. A manifest without it is refused before the broker
  starts. Declare exactly one mode, and never infer it from file or command
  names.

### `one_shot`

One process per EvalInput. Choose it when the entry point exits after each
response or rebuilds context from `messages`. Its `continuationProbe` declares:

- `secondInput`: an EvalInput whose `messages` carries an earlier transcript.
- `expectedSecondOutput`: the exact output the entry point returns for it.

Choose a `secondInput` whose correct answer is reachable only by reading
`messages`, for example a first turn that asks a question and a transcript that
already contains the answer. Verification runs the first turn, then
`secondInput` as a separate process, and requires the declared output exactly.
An entry point that ignores history fails here. When the agent's output varies
between runs, give `secondInput` an `outputSchema` that asks only for fields
that `messages` supply, and put only those fields in `expectedSecondOutput`.

### `resident_jsonl`

Only when one live process demonstrably keeps the application's real
conversation state across several requests. The entry point stays running for
one task and reads one JSONL request frame per stdin line
(`protocolVersion`, `requestId`, `sessionId`, `input`). It writes exactly one
JSONL result frame with the same `requestId` per input line. Its
`continuationProbe` declares `firstInput`, `secondInput`, and
`expectedSecondOutput`. Do not declare this mode for a one-shot CLI, and do not
emulate state in the adapter.

## Self-test the entry point

The broker discards the entry point's stderr, because it can carry customer
data. When verification's probe fails, `diagnostics` shows only the failure
code. So after the script writes the manifest and before it starts the broker,
it runs the entry point once itself with an empty input, `{}`, and stops with
the real error when that run fails. A failed boot's script output reaches
`diagnostics` with secrets scrubbed, so this is the error you repair from.

`{}` has no `task`, so a working entry point loads the application and answers
with its own `input` failure without running the agent or calling a model. The
script runs on every sandbox boot, so the self-test must stay that cheap. Never
give it the probe input.

Run it as `user` with the manifest's exact `cwd`, `argv`, and variable names,
and with the fixed environment the broker gives the entry point:

    selftest_err=/tmp/eval-selftest.err
    selftest_out="$(printf '{}' | runuser -u user -- env -i \
      HOME=/home/user USER=user LOGNAME=user LANG=C.UTF-8 \
      PATH=/usr/local/bin:/usr/bin:/bin NEOSIGMA_EVAL_SESSION_ID=setup-self-test \
      ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
      bash -c 'cd "$1" && shift && exec "$@"' _ "$HARNESS_CWD" "${HARNESS_ARGV[@]}" \
      2>"$selftest_err")" || true
    if ! printf '%s' "$selftest_out" | python3 -c \
      'import json,sys; r=json.loads(sys.stdin.read().splitlines()[-1]); sys.exit(r["failure"]["stage"] != "input")' \
      2>/dev/null; then
      tail -n 40 "$selftest_err"
      die "eval entry point self-test failed: ${selftest_out:-no output}"
    fi
    log "eval entry point self-test passed"

- Replace the `ANTHROPIC_API_KEY=` line with one `NAME="$NAME"` pair for each
  name in the manifest's `environment`, and no others.

## Start the broker

Stop a previous broker by its recorded handle, then start the broker and record
its PID in this exact order. Log paths may differ, but no command may run
between `&`, `$!`, and the PID-file write:

    broker=/usr/local/lib/neosigma/eval_adapter_reference.py
    broker_pid_file=/run/neosigma-eval-adapter.pid
    broker_stop=/usr/local/lib/neosigma/stop_eval_adapter.sh
    [ -x "$broker_stop" ] || die "eval adapter stop helper is unavailable"
    "$broker_stop" "$broker_pid_file" "$broker" 7391 \
      || die "prior eval adapter did not stop cleanly"
    nohup python3 /usr/local/lib/neosigma/eval_adapter_reference.py \
      >/tmp/eval-adapter.log 2>&1 &
    broker_pid=$!
    printf '%s\n' "$broker_pid" > "$broker_pid_file"
    chmod 0644 "$broker_pid_file"
    kill -0 "$broker_pid" 2>/dev/null \
      || die "eval adapter exited before its health gate"

- Never stop the broker with `pkill`, `killall`, or a name match. The stop
  helper checks that the recorded process is the broker and owns the loopback
  port before it signals anything.
- Do not move or rename `/run/neosigma-eval-adapter.pid`. Verification uses it
  to authenticate the live broker.
- The broker keeps `NEOSIGMA_EVAL_ADAPTER_TOKEN` to itself and runs the entry
  point as `user` with only the variables the manifest names. Never print,
  store, or pass that token.
- Health-gate the broker before the script exits. Its health response is fixed
  protocol metadata: it shows that the broker is up, and only the probe proves
  the harness runs. The script may also call the
  broker's `/probe` to prove the harness runs end to end. Give it a generous
  timeout, because a real probe makes live model calls. Verification runs its
  own probe after the script exits either way.

## Before you save

- When you can run the entry point where you author the script, run it once
  with a deterministic input and decode the result. `output` must be the
  application's real result. A successful status alone proves nothing.
- Confirm the manifest has `cwd`, `argv`, `environment`, `probe`, and an
  explicit `execution` mode with its continuation probe.
- Confirm the script self-tests the entry point with `{}` before it starts the
  broker.
- Confirm the broker lifecycle in the script: the stop helper call with the PID
  path, broker path, and port `7391`; `$!` captured right after the start; the
  PID written before the health check; and `kill -0` on the captured PID.
