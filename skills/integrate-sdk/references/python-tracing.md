# NeoSigma Python tracing integration

Instrument a Python codebase with `neosigma` so agent runs land in
NeoSigma as traces. This file is the API surface of record for the Python SDK
(0.4.x). Parameters not listed here do not exist. If something seems missing,
check https://docs.neosigma.ai/sdk/tracing rather than guessing.

## 1. Assess the codebase

Find, in this order:

- The real execution path: the route, job, queue consumer, or CLI command that
  runs the agent.
- The LLM surface: Anthropic Managed Agents, Claude Agent SDK, raw
  `anthropic`/`openai` clients, or something else.
- Existing OpenTelemetry: any `TracerProvider` construction or
  `trace.set_tracer_provider(...)` call. This decides the dual-export
  handling in section 5.
- The app's own ids: conversation/chat id, end-user id, and any per-request or
  per-run row id (these become `session_id`, `distinct_id`, `turn_id`).

Useful search:

```bash
rg -n "anthropic|openai|claude_agent_sdk|set_tracer_provider|TracerProvider|FastAPI|Starlette" .
```

## 2. Install and lifecycle

```bash
pip install neosigma                      # or: uv add neosigma
pip install "neosigma[instrumentation]"   # only if auto-instrumenting raw LLM clients
pip install "neosigma[fastapi]"           # only if using the FastAPI middleware
```

**Migrating from `neosigma-sdk`.** The distribution was renamed to `neosigma`
and the import path from `neosigma_sdk` to `neosigma`. The old names are frozen
at 0.7.0 and receive no further releases. Uninstall the old distribution,
install `neosigma`, then replace every `neosigma_sdk` import with `neosigma`.
Read the sections below against the current code rather than assuming the rest
of the surface is unchanged.

```python
import neosigma

neosigma.init()        # once at startup; reads NEOSIGMA_API_KEY from the env
...
neosigma.shutdown()    # once at process exit; flushes buffered spans
```

The exact `init` signature (no other keyword arguments exist; there is NO
`instrument` or `endpoint` argument):

```python
def init(
    api_key: str | None = None,
    *,
    project: str | None = None,
    tracing_enabled: bool | None = None,           # auto-instrumentation flag ONLY
    private_provider: bool | None = None,          # default True; False owns the global (section 5)
    tracer_provider: TracerProvider | None = None, # handoff: emit through a provider you built (section 5)
    settings: Settings | None = None,              # full config object, fields below
) -> None
```

- `init()` is idempotent and fail-open. It never raises into app startup; on
  any failure it logs and stays disabled.
- Without an API key the SDK exports nothing to NeoSigma, so the integration is
  safe to merge before keys are provisioned. (`console_export` still prints spans
  locally, and a `tracer_provider` handoff still emits through your provider; only
  the NeoSigma network sink is gated on the key.)
- Wire `shutdown()` into process exit: `atexit.register(neosigma.shutdown)`,
  or a FastAPI `lifespan` handler, or the worker's finally block. In a
  long-running server call it once on graceful stop, never per request.
  `neosigma.flush()` force-flushes without stopping. Note: `atexit` and
  `finally` do not run on an unhandled SIGTERM; in workers and containers,
  convert SIGTERM to a clean exit (e.g.
  `signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))`) so the flush runs.
- Call `shutdown()` once at exit, not mid-run. In the default private mode it
  is non-terminal: a later `init()` builds a fresh private provider and tracing
  resumes. In own mode a later `init()` cannot re-own the global (OTel allows it
  to be set only once) and falls back to a private provider, so tracing resumes
  but no longer captures process-global spans. In handoff mode it touches
  neither the provider nor its processors.

Settings fields / env vars (env prefix `NEOSIGMA_`): `api_key`, `project`
(default `default`), `service_name` (defaults to project), `otel_endpoint`
(default `https://otel.neosigma.ai/v1/traces`), `events_endpoint`
(default `https://otel.neosigma.ai/v1/events`), `enabled` (default true),
`tracing_enabled` (default false), `private_provider` (default true, env
`NEOSIGMA_PRIVATE_PROVIDER`), `capture_content`
(default true),
`max_content_chars` (default 24000), `console_export` (default false), plus
OTel batch knobs `max_queue_size`/`max_export_batch_size`/
`schedule_delay_millis`/`export_timeout_millis`.

## 3. Open a turn per user message

`turn()` is the core primitive: one user message, one trace, one `turn_id`.
Exact signature (keyword-only; there is NO `name`, `input`, `user_id`, or
`links` parameter):

```python
def turn(
    *,
    session_id: str = "",      # the conversation; generated sess_... if omitted
    user_message: str | None = None,
    distinct_id: str = "",     # the end user
    agent_id: str = "",
    source: str = "api",
    source_ref: str = "",      # opaque back-reference into the app's system
    turn_id: str = "",         # generated turn_... if omitted; SUPPLY the app's
                               # own request/run id when one exists (join key
                               # for product events)
) -> Turn
```

The `Turn` handle: `finish(output=None)` (records output, sets OK, ends span,
idempotent), `set_content(prompt=None, completion=None)`,
`set_attributes(mapping)`, and attributes `.turn_id` / `.session_id`. There is
NO `set_output`, `set_input`, or `end` method.

Prefer the context-manager form: it always finishes the turn (including when
the work raises) and records exceptions as errors, which the manual form does
not. Record output via `set_content` inside the block:

```python
# Handler shape: map the app's ids onto the turn.
with neosigma.turn(
    session_id=chat_id,
    distinct_id=user_id,
    turn_id=request_id,        # app's own id when it has one
    user_message=message_text,
) as t:
    result = run_agent(message_text)
    t.set_content(completion=result.output)
```

The manual form (`t = neosigma.turn(...)` then `t.finish(output=...)`) exists
for lifecycles a single block cannot wrap; if the work in between can raise,
guard it so `finish()` still runs, or the turn span leaks unfinished.

Opening a `turn()` inside an active turn does not fork a second trace; it
opens a child span reusing the outer ids, so wrapping is safe.

`turn()` opens the root span only. Model and tool spans come from sections 4a
to 4d.

## 4. Capture model and tool calls (pick per component)

### 4a. Anthropic Managed Agents

```python
client = neosigma.wrap_managed_agents(anthropic.Anthropic())  # or AsyncAnthropic
```

Use the wrapped client exactly as before (create session, stream events). The
adapter emits one turn per user message with `chat` and `execute_tool`
children, token usage included. No `turn()` needed around it; the adapter
rotates turns itself.

### 4b. Claude Agent SDK

```python
# For query():
async for message in neosigma.trace_claude(claude_agent_sdk.query(prompt=p)):
    ...
# Or a reusable drop-in:
traced_query = neosigma.wrap_claude_query()

# For the stateful ClaudeSDKClient (patches receive_response once):
neosigma.ClaudeTracingProcessor().configure()
```

`query()` is traced ONLY via these wrappers. A `from claude_agent_sdk import
query` binding is never patched; do not attempt to monkeypatch it.

### 4c. Raw Anthropic / OpenAI clients

```python
neosigma.init(tracing_enabled=True)   # requires the [instrumentation] extra
```

Client calls (`client.messages.create`, chat completions) become `chat` spans
under whichever turn is active. Instrumentation patches the library, not
individual clients, so clients constructed before `init()` are captured too.
`tracing_enabled` controls ONLY this auto-instrumentation; turns, decorators,
and adapters trace regardless.

### 4d. Tool functions and everything else

```python
@neosigma.tool()                 # execute_tool span per call; name defaults to
def search_kb(query: str): ...   # the function name, override: @neosigma.tool("kb")
```

Call arguments and the return value are captured as span content, subject to
the content capture setting in section 7. Prefer `@neosigma.tool()` over a
manual span for a tool call. A manual `span()` records only what you pass it,
so a tool traced that way shows its name with no input and no output.

When no decorator fits, such as a step inside a function or a call whose
boundary you do not control, open a span directly:

```python
from neosigma.spans import set_content   # not exported at the top level

with neosigma.span("rerank", input={"query": query, "candidates": 20}) as s:
    ranked = rerank(query, candidates)
    set_content(s, completion=json.dumps(ranked))
```

`neosigma.span(name, *, operation=None, attributes=None, input=None)` records
`input` and nothing else. A context manager has no return value to capture, so
write the output yourself with `set_content`. Use `start_chat()`/`end_chat()`
and `start_tool()`/`end_tool()` when the open and the close happen in different
functions.

The open and close pairs take typed models from `neosigma.types`:

- `start_chat(ChatStart(model, prompt_messages=None, thinking_mode=None))` opens
  a `chat` span. `model` is required.
- `end_chat(span, ChatEnd(completion=None, usage=None, latency_ms=None,
  is_error=False, error_message=None))` closes it. **`completion` is optional and
  nothing fails when it is missing, but the span then records no output.** Pass
  the model's reply here rather than writing it separately.
- `start_tool(ToolStart(name, args_json=None))` opens an `execute_tool` span.
  `name` is required.
- `end_tool(span, ToolEnd(result=None, is_error=False, error_message=None))`
  closes it.

Two more setters apply to a custom span, both from `neosigma.spans`.
`set_token_usage(span, usage)` attaches token counts, which `end_chat()` takes
as an argument instead. `set_correlation(span, *, turn_id="", distinct_id="",
session_id="", project="")` stamps correlation ids onto a span produced outside
an active turn, leaving any id you omit unset rather than blank.

`@neosigma.interaction()` traces a function as an `invoke_agent` root span but
does NOT create a turn (no ids); prefer `turn()` for the request boundary.
`@neosigma.turn_handler(session_from=..., message_from=..., output_from=...)`
wraps a handler whose arguments already carry the ids.

### 4e. FastAPI / Starlette

```python
from neosigma.integrations.fastapi import NeosigmaTurnMiddleware
app.add_middleware(NeosigmaTurnMiddleware)   # requires the [fastapi] extra
```

Opens a turn per request: `session_id` from the `x-neosigma-session-id`
request header, `distinct_id` from `x-neosigma-distinct-id`; returns the turn
id in the `x-neosigma-turn-id` response header. Optional kwargs: an async
`message_extractor(request)` for the user message and a `path_filter(path)`
to skip routes. It finishes turns without an output; use an explicit `turn()`
or `@turn_handler` in the handler instead when the response must be captured.

### 4f. LangChain

```python
from neosigma.integrations.langchain import neosigma_callback_handler  # requires the [langchain] extra
model = ChatAnthropic(model="claude-sonnet-4-6", callbacks=[neosigma_callback_handler()])
```

Pass the handler in any LangChain `callbacks` list (a model, chain, or agent
constructor, or a single `.invoke()`). Every model call, tool call, and chain
becomes a `chat`, `execute_tool`, or structural span, parented on LangChain's
run tree. One handler instance is reusable across concurrent invocations. It
does NOT open a turn, so wrap the top-level run in `turn()` for turn/session
correlation. Do NOT also enable raw-client auto-instrumentation (4c) for a model
that wraps an instrumented client (`langchain-anthropic` over `anthropic`,
`langchain-openai` over `openai`), or each call is recorded twice.

### 4g. Record where a tool stored a file

Use `neosigma.artifact()` to record the address of a file a tool produced.
NeoSigma stores the address only. It never reads, uploads, or copies the file,
so the value must be an address your own systems can resolve later. Requires
0.9.0 or later.

```python
@neosigma.tool()
def create_presentation(topic: str) -> str:
    path = f"decks/{topic}.pptx"
    storage.upload(path, build_deck(topic))
    neosigma.artifact(f"s3://artifacts/{path}", f"{topic}.pptx")
    return "Created your deck."
```

| Parameter | Default | Description |
| --- | --- | --- |
| `location` | Required | Where the artifact lives. Opaque to the SDK, so any address your systems resolve works, such as `s3://bucket/key` or `postgres://public.decks/9f3a`. |
| `name` | `None` | Display label for the trace UI. |

`artifact()` attaches to the span that is currently open, so call it inside a
`@neosigma.tool()` function, inside a `turn()`, or inside any active span.
Called with no active span, or before `init()`, it records nothing. Call it once
per artifact. Multiple calls on one span accumulate in call order, up to 100 per
span.

Artifacts follow the content capture setting in section 7. When
`capture_content` is `False`, no address is recorded. A location that is empty,
longer than `max_content_chars`, or not UTF-8 encodable is dropped. A name that
cannot be recorded is omitted while the entry keeps its location. Dropped
references are logged on the first drop, then once per 100.

## 5. Existing OpenTelemetry: coexistence and dual export

By default (`private_provider=True`) the SDK builds its OWN dedicated
`TracerProvider` and never registers it as the OTel global, so it runs
alongside an existing OpenTelemetry setup without changing it and without
capturing its spans. When the app already configures OpenTelemetry, the default
is correct: call `neosigma.init()` as usual, with no flag. There is NO
`attach_to_existing_provider` option; it was removed in 0.4.0.

The SDK's own spans (`turn()`, `@tool`, the adapters) and the auto-instrumentors
it scopes to its provider all reach NeoSigma under the private default, so the
agent trace is complete. Only genuinely foreign spans (the app's HTTP/DB
middleware) stay with the app's provider. Correlation to product events is by
attribute (`turn_id`/`session_id`), not by a shared trace tree.

Two non-default postures:

- **Own the global** (`private_provider=False`, env
  `NEOSIGMA_PRIVATE_PROVIDER=false`): register NeoSigma's provider as the OTel
  global and capture every span in the process, including the app's other
  instrumentation. If a real global provider is already set, the SDK does not
  clobber it: it logs a one-time notice and runs a private provider alongside
  instead. Tracing still works; it just does not capture the process-global
  spans. (It never goes dark next to an existing provider.)
- **Handoff** (`init(tracer_provider=provider)`): you build one provider and
  NeoSigma emits through it, owning nothing. This is Python's dual-export path.

### Dual export (Python: handoff)

To send the agent trace to NeoSigma AND another backend, build one provider
carrying NeoSigma's processors plus your backend's exporter, and hand it to
`init(tracer_provider=...)`. Both processors are public exports. ORDER MATTERS:
add `CorrelationSpanProcessor()` BEFORE `NeoSigmaSpanProcessor(api_key=...)`, so
it stamps `turn_id`/`session_id` on span start before the export processor sees
the span.

```python
import os
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from neosigma import CorrelationSpanProcessor, NeoSigmaSpanProcessor

provider = TracerProvider(resource=...)
provider.add_span_processor(CorrelationSpanProcessor())                                # FIRST
provider.add_span_processor(NeoSigmaSpanProcessor(api_key=os.environ["NEOSIGMA_API_KEY"]))  # THEN
provider.add_span_processor(BatchSpanProcessor(their_exporter))                        # your backend

neosigma.init(tracer_provider=provider)   # wires the turn helpers + event sink
```

Everything the SDK produces rides this provider, so it all reaches both backends:
`turn()`, `@tool`, the adapters, AND raw-client spans from
`init(tracing_enabled=True, tracer_provider=provider)` (auto-instrumentation is
pointed at the handed provider, so pass both arguments in the same `init()`).
Registering the provider as the OTel global is optional; NeoSigma emits through
the provider it was handed regardless, and correlation rides contextvars. Python
has no `extra_span_processors` option (that is TypeScript-only), so handoff is
the dual-export path. In handoff mode `shutdown()`/`flush()` touch neither the
provider nor its processors; drain the provider you built yourself with
`provider.shutdown()`.

Migration note: pre-0.4.0 used `attach_to_existing_provider=True` for dual
export. That option is removed. Passing it now raises `TypeError`. Use the
handoff provider above.

## 6. Cross-process and worker continuity

Correlation ids ride contextvars: they survive `await` but not a process,
thread-pool, or queue hop. Thread the ids through the job payload and re-bind
on the far side; do not rely on OTel context propagation for turn identity.
Choosing what to re-bind: a job that handles a NEW user message opens a new
`turn()` (same `session_id`, fresh turn id); deferred work for an EXISTING
message re-binds the SAME `turn_id` via `trace()` or `turn(turn_id=...)`.

```python
# Producer
enqueue(job, turn_id=t.turn_id, session_id=t.session_id, user_id=user_id)

# Consumer (separate process): same session, new or same turn
with neosigma.turn(session_id=job.session_id, distinct_id=job.user_id,
                   user_message=job.message):
    handle(job)
# Or, when spans are produced by an adapter and only ids must bind:
with neosigma.trace(turn_id=job.turn_id, session_id=job.session_id):
    handle(job)
```

Each process runs its own `init()`/`shutdown()`.

## 7. Content and privacy

`capture_content=False` (env `NEOSIGMA_CAPTURE_CONTENT=false`) records
metadata only (tokens, tool names, timings) and drops prompt/completion/tool
IO text everywhere, including adapters and auto-instrumentation.
`max_content_chars` (default 24000) truncates captured text per field.

## 8. Product events

Alongside tracing, the Python SDK emits product events that join the agent trace
on `turn_id`, so an app-side event (a click, a signup, a thumbs-up) sits next to
the model spans for the same turn.

- `capture(event_name, properties=None, *, turn_id=None)` records an event.
  Called inside a `turn()` (or a `trace()` id-binding scope) it inherits that
  scope's `turn_id`/`session_id`, so it joins that turn. Pass `turn_id=`
  explicitly to attach an event to a specific turn from outside a scope.
- `identify(distinct_id, properties=None)` attaches properties to an end user.

```python
with neosigma.turn(session_id=chat_id, distinct_id=user_id,
                   turn_id=request_id, user_message=text):
    reply = run_agent(text)
    neosigma.capture("message_sent", {"length": len(reply)})  # joins this turn
```

`capture()` queues the event and returns; delivery is batched in the background
to `NEOSIGMA_EVENTS_ENDPOINT`. Like tracing, it is a no-op without an API key.
The queue is tuned by `NEOSIGMA_EVENTS_MAX_QUEUE` (default 10000),
`NEOSIGMA_EVENTS_BATCH_SIZE` (default 100), and `NEOSIGMA_EVENTS_TIMEOUT_SECONDS`
(default 10).

## 9. Verify

1. Locally, set `NEOSIGMA_CONSOLE_EXPORT=true` and run one real request:
   spans print to stdout. Console export without an API key logs a warning
   that nothing reaches NeoSigma; it proves shape, not delivery.
2. With `NEOSIGMA_API_KEY` set, run one request, then check the traces page
   at https://platform.neosigma.ai. Confirm: one trace per user message; an
   `invoke_agent` root; `chat` children carrying token usage; `execute_tool`
   children for tools; the expected `session_id`/`turn_id`/`distinct_id`.
3. Dual export: also confirm the app's original backend still receives spans.

Troubleshooting:

| Symptom | Cause and fix |
| --- | --- |
| Nothing in NeoSigma, no errors | No `NEOSIGMA_API_KEY` in that environment, or `NEOSIGMA_ENABLED=false`. The SDK is silent by design; set the key. |
| Console spans print but platform is empty | Console export is on without a key (the SDK logs exactly this warning). Set the key. |
| NeoSigma not capturing the app's other (HTTP/DB) spans | Working as intended. The default provider is private, so NeoSigma traces only what it instruments, not the host's other spans. To capture every span, own the global with `init(private_provider=False)` (section 5). |
| `TypeError` on `init(attach_to_existing_provider=True)` | That option was removed in 0.4.0. For dual export, build a provider and use `init(tracer_provider=...)` (section 5). |
| Agent trace not reaching the app's own backend | Under the private default NeoSigma's spans go only to NeoSigma. For dual export, use the handoff provider in section 5. |
| Flat traces / spans missing a parent | The work is not running inside an active turn (thread/process hop, or no `turn()` opened). Re-bind ids per section 6. |
| `query()` calls produce no spans | The stream was not wrapped. Wrap with `trace_claude`/`wrap_claude_query`; imports of `query` are never patched. |
| Spans stop after some point in a run | Process exited without `shutdown()`; buffered spans were dropped. Wire section 2's lifecycle. |
