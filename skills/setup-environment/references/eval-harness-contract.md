# Eval harness contract

A setup script passes verification only when the booted stack exposes the
project's real agent to NeoSigma's evals through an
[Agent Client Protocol](https://agentclientprotocol.com) (ACP) adapter. The
script writes the adapter as a bundle at `/run/neosigma/acp-adapter`. After the
script exits, verification installs the bundle and holds two short
conversations with the adapter: the first checks that a conversation keeps its
state across turns, and the second checks that a new conversation does not see
the first one. A failure is reported as `health_probe`, with the reason in
`diagnostics` (`ACP check failed: ...`).

During an eval, a simulated user talks to the adapter over ACP. Each eval case
is one ACP session, and each user message is one ACP prompt.

Read [`boot-contract.md`](boot-contract.md) first. This file adds the harness
rules only.

## Find the real harness

Inspect the repository's API routes, CLI commands, request models, tests, and
local development scripts. Find the normal surface that runs the customer's
agent and returns its actual reply. In a multi-repository workspace, inspect
the sibling repositories too.

- Do not ask the user for a per-eval command, an expected output, or a
  hand-written adapter.
- When several independent agent surfaces exist, ask the user once which
  logical agent this project represents.
- A queue, an asynchronous job, a run-status endpoint, or an endpoint that
  creates another sandbox is not a harness. Those return scheduling state, not
  the agent's reply.
- When the repository has no direct way to run the agent and wait for its
  reply, the script calls `die` and the setup is not eval-ready. Never
  fabricate an adapter to pass.
- When the agent already speaks ACP natively, or the project ships a
  maintained ACP adapter for this same agent, use it. Otherwise write a thin
  adapter with the official ACP SDK. Never replace a custom agent with the
  model CLI or SDK it uses underneath: the eval must run the customer's own
  prompts, tools, model selection, and tracing.

## The adapter

The adapter is an ACP agent on standard input and output. It sends diagnostics
only to standard error. It must:

- Create one isolated application conversation for each ACP session, and
  continue that same conversation for every later prompt in the session.
- Keep conversations apart. Nothing from one session may reach another,
  including state the harness keeps on disk. For example, a harness built on
  Claude Code keeps memory in its configuration directory: give each session
  its own `CLAUDE_CONFIG_DIR`. Verification fails with "leaked conversation
  state into a fresh session" when a new session can recall an earlier one.
- Send only the customer-visible reply as ACP agent message updates. Keep
  internal prompts, reasoning, tool calls, tool results, and credentials out of
  them.
- Finish a prompt only when the application's turn finishes. Report a failure
  as an ACP error with a specific reason, never as a made-up reply.
- Implement cancellation with the harness's real mechanism: the application's
  cancel endpoint, or ending the turn's process group. Closing an HTTP stream
  does not prove that server-side work stopped. After `session/cancel`, end the
  pending prompt with stop reason `cancelled`, even when the harness cannot stop
  its work. Never retry a submitted turn automatically.
- Wait for the whole application turn. Do not use an HTTP client whose default
  limit is shorter than a turn: Node's `fetch` gives up after 5 minutes.
- When `NEOSIGMA_EVAL_CONTEXT_PATH` is set, read the JSON file it names when
  the session starts. Its `input_data` is the original eval request,
  including application fields beyond the user message (for example an
  account or record id). Pass those fields to the application with every
  message. The ACP prompt carries the current user message. Never send the
  file's contents back as a reply. Verification does not set this variable,
  so the adapter must work without it.
- Find its own files relative to its launcher, because NeoSigma installs the
  verified bundle in another directory. Read application settings from the
  environment in `runtime.json`.
- Keep the trial identity NeoSigma sets: `NEOSIGMA_PROJECT` and every
  `NEOSIGMA_EVAL_*` variable, such as `NEOSIGMA_EVAL_SESSION_ID`. Pass them
  unchanged to every process the adapter starts, and start the application's
  services from the setup script with the environment it receives. The
  application's tracing uses them to attach each trial's trace, cost, and
  tokens to the experiment. Never replace `NEOSIGMA_EVAL_SESSION_ID` with the
  ACP session id.

Two shapes cover most agents:

- **Conversation API.** The application has endpoints to create a
  conversation, send a message, and stream or return the reply, and usually
  to cancel a turn. Map each ACP session to one application conversation, and
  relay each prompt as one message.
- **One-turn entry point.** The repository has a command or module that runs
  one turn of the agent and accepts the earlier turns. The adapter keeps each
  session's transcript, runs the entry point once per prompt with the earlier
  turns, and returns its reply. Cancel ends that turn's process group.

## The bundle

Write a fresh bundle on every boot:

    /run/neosigma/acp-adapter/
      acp-adapter.json   specification (root, readable)
      runtime.json       launch values (root, mode 0600)
      payload/           adapter code, its launcher, vendored dependencies, notices

- `payload/` holds only the adapter, its launcher, its bundled dependencies,
  and their licence notices. Never put credentials, logs, databases, copies of
  the repository, rubrics, or expected answers in it.
- `runtime.json` is
  `{"working_directory": "<absolute path>", "environment": {"NAME": "value"}}`:
  the working directory and the complete environment the adapter starts with.
  It is the only environment the adapter gets, so include `HOME`, `LANG`,
  `PATH`, and every value the application needs, including secrets read from
  the script's environment and values the script generated for local
  services. Write it as root with mode `0600`, never inside `payload/`.
  NeoSigma reads it privately and never keeps it with the bundle. Pass secret
  values to the code that writes it through the environment, never as command
  arguments.
- The adapter runs as the sandbox user `user`, never as root.

`acp-adapter.json` must validate against
[`acp-adapter.schema.json`](acp-adapter.schema.json):

- `schema_version`: `1`.
- `inputs`: `schema_version` `1`; `platforms`, the real Linux platform of the
  adapter's code (`os`, `architecture`, `libc`, `loader`, and `minimum_libc`
  for glibc); `sources`, every input file as `{"name", "digest"}` with a
  `sha256:` digest, sorted by name; and `versions`, the exact version of the
  interpreter and of every bundled package.
- `entrypoint`: `name`, `path` (the launcher's path relative to `payload/`),
  and optional `arguments`.
- `notices`: the licence notice files, as paths relative to `payload/`.
- `probe`: `first_prompt` and `recall_prompt`. Put `{nonce}` only in
  `first_prompt`, and word both prompts the way a user of this application
  would ask it to remember a reference and later ask for it back. NeoSigma
  supplies the random value.

Do not add archive hashes, organization ids, storage locations, or the setup
script's hash. NeoSigma computes those.

## Self-test the adapter

After writing the bundle, start the adapter once as `user` with exactly the
values in `runtime.json` (and nothing else), and send ACP `initialize` and
`session/new`. Stop with the adapter's error when either fails. This proves the
adapter and its dependencies load without calling a model, so it stays cheap on
every boot. Do not send a prompt here: verification runs the real
conversation after the script exits.

## Complete adapter stage

This stage, for the one-turn entry point shape, passed verification. It
vendors the official Python ACP SDK at pinned versions with checked digests and
runs on the sandbox's system Python 3.10. `PRIMARY` is the primary repository
path, and `$PRIMARY/.venv/bin/python3 -m app.agent_turn` stands for the
repository's real one-turn entry point, which reads
`{"task": ..., "messages": [...]}` on stdin and prints its reply. Replace that
call and the environment names with this application's own. For the
conversation API shape, keep the bundle and self-test steps and replace the
`prompt` and `cancel` methods with calls to the application's conversation
endpoints.

```bash
BUNDLE=/run/neosigma/acp-adapter
PAYLOAD="$BUNDLE/payload"
ADAPTER_PYTHON=/usr/bin/python3
TARGET_HOME=/home/user
TARGET_PATH="/usr/local/bin:/usr/bin:/bin"

log "writing the ACP adapter bundle at $BUNDLE"
rm -rf -- "$BUNDLE"
install -d -o root -g root -m 0755 /run/neosigma "$BUNDLE" "$PAYLOAD" "$PAYLOAD/bin"

cat > "$PAYLOAD/agent_acp.py" <<'PY'
"""ACP agent over the application's one-turn entry point; one session is one conversation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from acp import PROTOCOL_VERSION, RequestError, run_agent, text_block, update_agent_message  # noqa: E402
from acp.interfaces import Client  # noqa: E402
from acp.schema import Implementation, InitializeResponse, NewSessionResponse, PromptResponse, TextContentBlock  # noqa: E402

STOP_GRACE_SECONDS = 10
log = logging.getLogger("agent-acp")


class Conversation:
    def __init__(self, fields: dict[str, Any]) -> None:
        self.fields = fields
        self.messages: list[dict[str, str]] = []
        self.process: asyncio.subprocess.Process | None = None
        self.cancelled = False
        self.state_directory = tempfile.mkdtemp(prefix="agent-acp-")


def eval_fields() -> dict[str, Any]:
    path = os.environ.get("NEOSIGMA_EVAL_CONTEXT_PATH")
    if not path:
        return {}
    try:
        input_data = json.loads(Path(path).read_text(encoding="utf-8"))["input_data"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RequestError.internal_error({"reason": "the eval context file is unreadable"}) from error
    if not isinstance(input_data, dict):
        return {}
    return {key: value for key, value in input_data.items() if key not in ("task", "messages")}


class Agent:
    def __init__(self) -> None:
        self.client: Client | None = None
        self.conversations: dict[str, Conversation] = {}

    def on_connect(self, conn: Client) -> None:
        self.client = conn

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=min(protocol_version, PROTOCOL_VERSION),
            agent_info=Implementation(name="agent-acp", title="Agent", version="1.0.0"),
        )

    async def new_session(self, cwd: str, **kwargs: Any) -> NewSessionResponse:
        session_id = f"acp-{os.urandom(8).hex()}"
        self.conversations[session_id] = Conversation(eval_fields())
        return NewSessionResponse(session_id=session_id)

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        conversation = self.conversations.get(session_id)
        if conversation is None:
            raise RequestError.resource_not_found(session_id)
        if conversation.process is not None:
            raise RequestError.invalid_request({"reason": "a turn is already running in this session"})
        if not all(isinstance(block, TextContentBlock) for block in prompt):
            raise RequestError.invalid_params({"reason": "only text messages are supported"})
        message = "\n".join(block.text for block in prompt)
        request = {**conversation.fields, "task": message, "messages": list(conversation.messages)}
        conversation.cancelled = False
        conversation.process = await asyncio.create_subprocess_exec(
            os.environ["AGENT_PYTHON"], "-m", "app.agent_turn",
            cwd=os.environ["AGENT_CWD"],
            # Per-conversation state directory; name the harness's own variable.
            env={**os.environ, "CLAUDE_CONFIG_DIR": conversation.state_directory},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, stderr = await conversation.process.communicate(json.dumps(request).encode())
            code = conversation.process.returncode
        finally:
            conversation.process = None
        if conversation.cancelled:
            return PromptResponse(stop_reason="cancelled")
        if code != 0:
            log.error("turn_failed exit=%s stderr=%s", code, stderr.decode(errors="replace")[-2000:])
            raise RequestError.internal_error({"reason": f"the agent turn exited {code}"})
        reply = stdout.decode().strip()
        if self.client is not None:
            await self.client.session_update(session_id=session_id, update=update_agent_message(text_block(reply)))
        conversation.messages += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        conversation = self.conversations.get(session_id)
        process = conversation.process if conversation is not None else None
        if conversation is None or process is None or process.returncode is not None:
            return
        conversation.cancelled = True
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    agent = Agent()
    try:
        asyncio.run(run_agent(agent))
    finally:
        for conversation in agent.conversations.values():
            shutil.rmtree(conversation.state_directory, ignore_errors=True)


if __name__ == "__main__":
    main()
PY

cat > "$PAYLOAD/bin/agent-acp" <<SH
#!/bin/sh
exec $ADAPTER_PYTHON -I -B "\${0%/*}/../agent_acp.py" "\$@"
SH
chmod 0644 "$PAYLOAD/agent_acp.py"
chmod 0755 "$PAYLOAD/bin/agent-acp"

# Vendor the ACP SDK at pinned versions, check every download's SHA-256, and write
# acp-adapter.json and the private runtime.json. Secrets come from the environment.
AGENT_CWD="$PRIMARY" AGENT_PYTHON="$PRIMARY/.venv/bin/python3" \
"$ADAPTER_PYTHON" -I - "$BUNDLE" "$TARGET_HOME" "$TARGET_PATH" <<'PY'
import hashlib
import json
import os
import platform
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

bundle, home, path = sys.argv[1:]
payload = Path(bundle) / "payload"
vendor = payload / "vendor"
files = "https://files.pythonhosted.org/packages"
architecture = platform.machine()
loaders = {"x86_64": "/lib64/ld-linux-x86-64.so.2", "aarch64": "/lib/ld-linux-aarch64.so.1"}
if architecture not in loaders:
    sys.exit(f"unsupported architecture {architecture}")
if sys.version_info[:2] != (3, 10):
    sys.exit(f"the adapter needs the image's Python 3.10, found {platform.python_version()}")
core = {
    "x86_64": ("ba/1e/acc4d70f88a0a277e4a1fa77ebb985ceabaf900430f875bf9338e11c9420", "x86_64", "395aebd9183f9d112f569aeb5b2214d1a10a33bec8456447f7fbdfa51d38d4cd"),
    "aarch64": ("64/ba/bfb1d928fd5b49e1258935ff104ae356e9fd89384a55bf9f847e9193ad40", "aarch64", "bb63e0198ca18aad131c089b9204c23079c3afa95487e561f4c522d519e55aba"),
}[architecture]
wheels = [
    ("agent-client-protocol", "0.12.1", "21/61/0a2186c8ca53cf464767d4c4fb7fd9e23f48ce4547c2fa0d1c83a4d52410/agent_client_protocol-0.12.1-py3-none-any.whl", "aaf3cc301ed87d9e2c4ccb91adfe1cd58784355f6ce7e513f50e7ca2391862ef"),
    ("annotated-types", "0.7.0", "78/b6/6307fbef88d9b5ee7421e68d78a9f162e0da4900bc5f5793f6d3d0e34fb8/annotated_types-0.7.0-py3-none-any.whl", "1f02e8b43a8fbbc3f3e0d4f0f4bfc8131bcb4eebe8849b8e5c773f3a1c582a53"),
    ("pydantic", "2.13.4", "fd/7b/122376b1fd3c62c1ed9dc80c931ace4844b3c55407b6fb2d199377c9736f/pydantic-2.13.4-py3-none-any.whl", "45a282cde31d808236fd7ea9d919b128653c8b38b393d1c4ab335c62924d9aba"),
    ("pydantic-core", "2.46.4", f"{core[0]}/pydantic_core-2.46.4-cp310-cp310-manylinux_2_17_{core[1]}.manylinux2014_{core[1]}.whl", core[2]),
    ("typing-extensions", "4.16.0", "49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80/typing_extensions-4.16.0-py3-none-any.whl", "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),
    ("typing-inspection", "0.4.4", "67/81/4add07e5172b7ac40d8ed5ff580409a7801a4fe26d529bdd915401dabfbe/typing_inspection-0.4.4-py3-none-any.whl", "65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147"),
]


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


sources = {}
with tempfile.TemporaryDirectory() as downloads:
    for name, version, location, expected in wheels:
        filename = location.rsplit("/", 1)[1]
        with urllib.request.urlopen(f"{files}/{location}", timeout=60) as response:
            content = response.read()
        if digest(content) != f"sha256:{expected}":
            sys.exit(f"{filename} does not match its pinned SHA-256")
        archive = Path(downloads) / filename
        archive.write_bytes(content)
        with zipfile.ZipFile(archive) as wheel:
            wheel.extractall(vendor)
        sources[filename] = digest(content)
for name in ("agent_acp.py", "bin/agent-acp"):
    sources[f"adapter/{name}"] = digest((payload / name).read_bytes())

notices = sorted(p.relative_to(payload).as_posix() for p in vendor.glob("*.dist-info/licenses/*") if p.is_file())
if len({notice.split("/")[1] for notice in notices}) != len(wheels):
    sys.exit("every vendored package must ship its licence notice")

specification = {
    "schema_version": 1,
    "inputs": {
        "schema_version": 1,
        "platforms": [
            {"os": "linux", "architecture": architecture, "libc": "glibc", "loader": loaders[architecture], "minimum_libc": [2, 17]}
        ],
        "sources": [{"name": name, "digest": sources[name]} for name in sorted(sources)],
        "versions": {"python": platform.python_version(), **{name: version for name, version, _, _ in wheels}},
    },
    "entrypoint": {"name": "agent", "path": "bin/agent-acp", "arguments": []},
    "notices": notices,
    "probe": {
        "first_prompt": "Please remember the reference {nonce} for later in this conversation.",
        "recall_prompt": "What reference did I ask you to remember?",
    },
}
(Path(bundle) / "acp-adapter.json").write_text(json.dumps(specification, indent=2) + "\n", encoding="utf-8")

environment = {
    "HOME": home,
    "LANG": "C.UTF-8",
    "PATH": path,
    "AGENT_CWD": os.environ["AGENT_CWD"],
    "AGENT_PYTHON": os.environ["AGENT_PYTHON"],
    "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"],
}
runtime = {"working_directory": os.environ["AGENT_CWD"], "environment": environment}
descriptor = os.open(Path(bundle) / "runtime.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    json.dump(runtime, stream, indent=2)
PY
chown -R root:root "$BUNDLE"
chmod -R u=rwX,go=rX "$BUNDLE"
chmod 0600 "$BUNDLE/runtime.json"

log "checking the adapter answers ACP initialize and session/new as user"
timeout 60 runuser -u user -- env -i HOME="$TARGET_HOME" LANG=C.UTF-8 PATH="$TARGET_PATH" \
  AGENT_CWD="$PRIMARY" AGENT_PYTHON="$PRIMARY/.venv/bin/python3" \
  "$ADAPTER_PYTHON" -I - "$PAYLOAD/bin/agent-acp" "$PRIMARY" <<'PY' \
  || die "the ACP adapter did not complete initialize and session/new (its diagnostics are above)"
import json
import subprocess
import sys

launcher, cwd = sys.argv[1:]
requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1, "clientCapabilities": {}}},
    {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": cwd, "mcpServers": []}},
]
adapter = subprocess.Popen([launcher], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
try:
    for request in requests:
        adapter.stdin.write(json.dumps(request) + "\n")
        adapter.stdin.flush()
        response = json.loads(adapter.stdout.readline())
        if response.get("id") != request["id"] or "result" not in response:
            sys.exit(f"{request['method']} failed: {response}")
    if not response["result"].get("sessionId"):
        sys.exit("session/new returned no session id")
finally:
    adapter.stdin.close()
    adapter.wait(timeout=30)
PY

[ "$(stat -c '%U %a' "$BUNDLE/runtime.json")" = "root 600" ] || die "runtime.json must be root-owned with mode 0600"
log "ACP adapter bundle ready at $BUNDLE"
```

The self-test passes values on the command line only because they are not
secret. Pass every value the application needs at start-up in the same way,
and keep secrets in `runtime.json`.

## Before you save

- The adapter runs the customer's real agent, not the model it uses.
- Each ACP session is one conversation; a second prompt in a session continues
  it, and a new session starts empty, including any state the harness keeps on
  disk.
- Failures are ACP errors with a reason. Cancel uses the harness's real
  mechanism.
- `acp-adapter.json` validates against the schema, `runtime.json` is root-owned
  with mode `0600` and outside `payload/`, and every bundled file is in
  `sources` with its digest.
- The script self-tests the adapter with `initialize` and `session/new` as
  `user`, with only the `runtime.json` values.
