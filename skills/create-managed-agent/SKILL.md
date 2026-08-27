---
name: create-managed-agent
description: Create and run a NeoSigma Managed Agent with Python's neosigma package. Use for agent definitions, environments, sessions, and event polling; not tracing or unsupported live streaming and sandbox controls.
---

# Create a NeoSigma Managed Agent

Start with the [package](https://pypi.org/project/neosigma/),
[SDK source](https://github.com/neosigmaai/neosigma-sdk/tree/main/python), and
[documentation](https://docs.neosigma.ai). Explore the installed client or
source before extending it: `NeoSigma` exposes `agents`, `environments`, and
`sessions`; use `sessions.events.list()` for progress, not a guessed stream.

Quick steps: configure `NEOSIGMA_API_KEY`; find or create the environment and
agent; create a session with its first task; poll status and events to a settled
state; persist IDs and report the evidence.

Use the public `NeoSigma`/`AsyncNeoSigma` client from `neosigma`, never
`neosigma.agents._generated`. Identify the project ID and whether to reuse an
agent/environment before making changes. If the model, harness, or task type is
unknown, ask; do not invent them. Keep credentials out of code, metadata,
instructions, messages, and MCP endpoints.

```bash
pip install neosigma
export NEOSIGMA_API_KEY="ns_live_..."
```

```python
import time
from neosigma import NeoSigma

PROJECT_ID = "YOUR_PROJECT_ID"

with NeoSigma(timeout=60.0) as client:
    environment = client.environments.create(
        project_id=PROJECT_ID, name="repository-maintenance",
        description="Runtime for focused repository tasks.",
    ).environment
    agent = client.agents.create(
        project_id=PROJECT_ID, name="Repository maintainer",
        instructions=("Work only on the stated task. Inspect relevant files first. "
                      "Make the smallest complete change. Run relevant checks."),
        model={"provider": "anthropic", "id": "claude-sonnet-4-5"},
        harness="claude", task_type="create_pr",
    ).agent
    session = client.sessions.create(
        agent_id=agent.id, environment_id=environment.id,
        messages=[{"content": "YOUR_TASK"}],
    ).session
    while client.sessions.get(session.id).session.status not in {
        "idle", "interrupted", "failed"
    }:
        time.sleep(1)
    cursor = None
    while True:
        page = client.sessions.events.list(session_id=session.id, limit=50, cursor=cursor)
        for event in page.items:
            print(event.seq, event.type, event.payload)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
```

Agent instructions are the durable contract: define role, scope, safety limits,
completion criteria, and report. Put only the one-off task in a session message.
Use least privilege for task type and `mcp_servers[].allowed_tools`.

Create an idle session by omitting `messages`; use
`sessions.events.send(session_id=..., messages=[{"content": ...}])` for later
turns. Poll event lists at a bounded interval, deduplicate by increasing `seq`,
and do not treat an empty page or an accepted interrupt as completion.

`agents.update()` creates a full immutable version: omitted `skills`,
`subagents`, `mcp_servers`, and `metadata` become empty. Existing sessions stay
pinned. For environment updates, read first and send `expected_version`; on
conflict re-read. Do not blindly retry a failed `events.send()`—read the event
log first because the service may have accepted it.

Use `AsyncNeoSigma` with `async with`, await all calls, and use `asyncio.sleep`.
The current SDK has event-list polling only: do not claim live streaming,
sandbox or secret controls, session budgets, typed event payloads, or
per-session version selection. Report IDs, selected model/harness/task type,
settled status, and event evidence.
