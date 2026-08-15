# NeoSigma TypeScript tracing integration

Instrument a TypeScript/Node codebase with the npm package `neosigma` so
agent runs land in NeoSigma as traces. This file is the API surface of record
for the TypeScript SDK (0.9.0 or later). Parameters and exports not listed here do not
exist. If something is missing, stop rather than guessing.

The TypeScript SDK does full agent tracing (turns, framework adapters, dual
export), not only product events. Product events (`capture`/`identify`) are in
[typescript-events.md](typescript-events.md); the two share one process and one
id model, so instrument tracing here and bind events there.

## 1. Assess the codebase

Find, in this order:

- The real execution path: the route, job, queue consumer, or CLI command that
  runs the agent. Instrument that, not a new demo.
- The LLM surface, which decides section 4: Vercel AI SDK (`ai`), LangChain
  (`@langchain/*`), the Claude Agent SDK (`@anthropic-ai/claude-agent-sdk`),
  Anthropic Managed Agents (`@anthropic-ai/sdk` beta sessions), or raw model
  clients.
- Existing OpenTelemetry: any `NodeTracerProvider`/`BasicTracerProvider`
  construction or `trace.setGlobalTracerProvider(...)`. This decides section 5.
- The app's own ids: conversation id, end-user id, and any per-request or
  per-run id (these become `sessionId`, `distinctId`, `turnId`).
- Whether captured prompts, completions, or tool payloads contain sensitive
  data. Set content capture accordingly (section 7).
- Runtime and module system: long-running server, serverless, or CLI (decides
  the flush strategy, section 2); ESM vs CommonJS; Node or Bun.

```bash
rg -n "from \"ai\"|@ai-sdk|@langchain|claude-agent-sdk|@anthropic-ai/sdk|setGlobalTracerProvider|NodeTracerProvider|experimental_telemetry" .
```

Runtime facts: the SDK ships both ESM and CommonJS entrypoints and uses
OpenTelemetry JS 2.x, which requires Node `^18.19.0 || >=20.6.0`. Use the
codebase's native module style:

```ts
// ESM
import { init, turn } from "neosigma";

// CommonJS
const { init, turn } = require("neosigma");
```

### Next.js (Node runtime only)

Use the SDK only in Node.js server code: route handlers, server actions, server
components with `export const runtime = "nodejs"`, jobs, or the Next
`instrumentation.ts` hook. Do not import it from client components, middleware,
or routes configured for the Edge runtime.

Initialize once in `instrumentation.ts`; the runtime guard keeps the Node-only
OpenTelemetry dependencies out of Edge builds:

```ts
// instrumentation.ts
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { init } = await import("neosigma");
    init();
  }
}
```

Then import `turn`, adapters, and event helpers normally from
`"neosigma"` in the Node.js code path that runs the agent. Finish by
running `next build`; an integration is not complete until that production
build and one instrumented request succeed.

## 2. Install and lifecycle

```bash
npm install neosigma
```

**Migrating from `neosigma-sdk`.** The npm package was renamed to `neosigma`.
The old name is frozen at 0.7.0 and receives no further releases. Uninstall
`neosigma-sdk`, install `neosigma`, then update every import. Read the sections
below against the current code rather than assuming the rest of the surface is
unchanged.

```ts
import { init, shutdown } from "neosigma";

init(); // once at startup; reads NEOSIGMA_API_KEY; idempotent, never throws
// ... run the agent ...
await shutdown(); // once at process exit; flushes buffered spans
```

The `init` options (no other keys exist; there is NO `tracer`, `instrument`, or
`endpoint` option):

```ts
function init(overrides?: {
  apiKey?: string;
  project?: string;
  tracingEnabled?: boolean;            // optional raw-client auto-instrumentation (section 4)
  privateProvider?: boolean;           // default true, builds a private provider (section 5)
  tracerProvider?: TracerProvider;     // hand NeoSigma a provider you built (section 5)
  eventsEndpoint?: string;
  otelEndpoint?: string;               // MUST be the full path ending /v1/traces
  consoleExport?: boolean;
  enabled?: boolean;
  extraSpanProcessors?: SpanProcessor[]; // dual export, section 5
  settings?: Partial<Settings>;        // any other setting by its camelCase name
}): void;
function initAsync(overrides?: Parameters<typeof init>[0]): Promise<void>;
```

- `init()` is idempotent and fail-open. It never throws into app startup; on any
  failure it degrades to inactive and the app runs unchanged.
- Without an API key (and without `consoleExport`) no telemetry pipeline is
  initialized and nothing is sent. Ids are still generated and wrapped
  application calls run normally.
- `otelEndpoint` is the FULL OTLP path. Its default is
  `https://otel.neosigma.ai/v1/traces`. If you set it from a base URL, append
  `/v1/traces` yourself, or exports 404.

Environment variables (prefix `NEOSIGMA_`, `SCREAMING_SNAKE_CASE`):
`NEOSIGMA_API_KEY`, `NEOSIGMA_PROJECT` (default `default`),
`NEOSIGMA_SERVICE_NAME` (defaults to project), `NEOSIGMA_OTEL_ENDPOINT`,
`NEOSIGMA_EVENTS_ENDPOINT`, `NEOSIGMA_ENABLED` (default true),
`NEOSIGMA_TRACING_ENABLED` (default false), `NEOSIGMA_PRIVATE_PROVIDER`
(default true), `NEOSIGMA_CONSOLE_EXPORT` (default false),
`NEOSIGMA_CAPTURE_CONTENT` (default true), `NEOSIGMA_MAX_CONTENT_CHARS`
(default 24000), plus OTel batch knobs `NEOSIGMA_MAX_QUEUE_SIZE`,
`NEOSIGMA_MAX_EXPORT_BATCH_SIZE`, `NEOSIGMA_SCHEDULE_DELAY_MILLIS`,
`NEOSIGMA_EXPORT_TIMEOUT_MILLIS`.

### Flush strategy by runtime shape (spans are lost without this)

Spans are batched and exported on a timer (default 5000 ms). A process that
exits before the timer fires loses every unflushed span. This is the single
most common reason a correct-looking integration produces no traces. Pick by
runtime shape:

- **Long-running server:** call `installShutdownHandlers()` once at startup. It
  registers SIGTERM/SIGINT handlers that flush spans AND events, then re-raise
  the signal so the process still exits. It coordinates across multiple loaded
  copies of the SDK (for example Next.js split server/instrumentation bundles),
  so every copy flushes on one signal. Keep the app's own HTTP-drain shutdown.
- **Serverless / per-invocation:** `await flush()` before returning from the
  handler. The background timer will not fire before the platform freezes the
  instance. `flush()` does not stop the SDK, so the next invocation still works.
- **CLI / script:** `await shutdown()` at the end.

Call `shutdown()` once at exit, never mid-run. In the default private mode a
later `init()` rebuilds a fresh private provider, so tracing can resume. In own
mode (section 5) a later `init()` cannot re-own the global (OpenTelemetry sets
it once), so it falls back to a private provider. NeoSigma tracing resumes, but
no longer captures process-global spans.

```ts
function flush(): Promise<void>;
function shutdown(): Promise<void>;
function installShutdownHandlers(): void;
function isInitialized(): boolean;
```

## 3. Open a turn per user message

`turn()` is the core primitive: one user message, one trace, one `turnId`.

```ts
function turn<T>(opts: TurnOptions, fn: (t: Turn) => T): T;

interface TurnOptions {
  sessionId?: string;   // the conversation; generated sess_... if omitted
  userMessage?: string; // captured as prompt content (privacy-gated)
  distinctId?: string;  // the end user
  agentId?: string;
  source?: string;      // default "api"
  sourceRef?: string;   // opaque back-reference into the app's system
  turnId?: string;      // generated turn_... if omitted; SUPPLY the app's own
                        // request/run id when one exists (join key for events)
}
```

`turn(opts, fn)` runs `fn` (sync or async), ends the turn when `fn` returns or
its promise settles, and records a thrown error as an ERROR-status span and
re-throws. It returns whatever `fn` returns.

```ts
import { turn } from "neosigma";

const answer = await turn(
  { sessionId: chatId, distinctId: userId, turnId: requestId, userMessage: text },
  async (t) => {
    const result = await runAgent(text); // model + tool spans nest here
    t.finish({ output: result.reply });  // record the output; see the pitfall below
    return result;
  },
);
```

**Pitfall: the callback return value is NOT recorded as the turn output.**
`turn(opts, fn)` passes `fn`'s return value straight back to the caller and does
not capture it as content (a handler usually returns a response object, not the
reply text). To record output, call `t.finish({ output })` before returning, or
use `turnHandler` (section 4e), whose `outputFrom` records the return value by
default.

The `Turn` handle (passed into the callback):

```ts
class Turn {
  readonly turnId: string;
  readonly sessionId: string;
  finish(opts?: { output?: string }): void;        // records output, ends span, idempotent
  setContent(content: { prompt?: string; completion?: string }): void;
  setAttributes(attrs: Record<string, AttributeValue>): void;
}
```

There is NO `setOutput`, `setInput`, or `end` method, and no handle-form opener:
`turn(opts, fn)` is callback-only (this keeps span nesting reliable across
`await`). For work a single callback cannot wrap, for example a conversation
whose turns open in separate request handlers, give each scope its own `turn()`
and share a `sessionId`. One turn is one trace, and a conversation is a session
of turns, so the scopes stay correlated through the shared session without a
long-lived handle. To bind ids onto spans produced outside any `turn()`, use
`trace({ turnId })` (section 6).

Opening a `turn()` inside an active turn does not fork a second trace; it opens
a child span reusing the outer ids, so wrapping is safe.

`turn()` opens the root span only. Model and tool spans come from section 4. A
turn around uninstrumented work is a root span with no children.

### HTTP turn middleware (open a turn per request)

To open the turn at the HTTP layer instead of calling `turn()` in each handler,
wrap the server once. Both read `x-neosigma-session-id` and
`x-neosigma-distinct-id` from the request and set the turn id on the
`x-neosigma-turn-id` response header. Neither reads a client-supplied turn id, so
a caller cannot write into another turn's trace.

```ts
import { expressTurnMiddleware, withTurn } from "neosigma";

// Express: mount AFTER express.json() (messageExtractor reads req.body).
app.use(expressTurnMiddleware({ messageExtractor: (req) => req.body?.message }));

// Fetch-shaped handlers (Next.js App Router, Bun, Cloudflare Workers, Deno, Hono):
export const POST = withTurn(handler, {
  messageExtractor: async (req) => (await req.json()).message, // req is a clone
  outputFrom: async (res) => (await res.json()).reply, // res is a clone
});
```

`expressTurnMiddleware` marks the turn errored on a `>= 500` response. Pass
`pathFilter(path)` to skip routes. `withTurn` wraps a `(Request) => Response`
handler and cannot run in Next `middleware.ts`/`instrumentation.ts`, so wrap the
route handler itself. Its `messageExtractor` and `outputFrom` receive clones, so
reading their bodies does not disturb the handler. For Hono, wrap the raw
request with `app.post("/chat", (c) => withTurn(handler)(c.req.raw))`.

## 4. Capture model and tool calls (pick per component)

Choose exactly one capture path for each model call. When the app uses a
supported framework, use its explicit adapter below. For direct calls through
the raw `openai` or `@anthropic-ai/sdk` client, install only the matching
instrumentor and enable auto-instrumentation at startup:

```bash
npm install @traceloop/instrumentation-anthropic # @anthropic-ai/sdk
npm install @traceloop/instrumentation-openai    # openai
```

Initialize the instrumentor before loading the raw provider client so its Node
module hook is ready first. Bundlers require a runtime-specific recipe; the
tested Next.js path is below.

```ts
import { initAsync } from "neosigma";

await initAsync({ tracingEnabled: true });
const { default: OpenAI } = await import("openai");
const client = new OpenAI();
```

The SDK skips a provider that is not installed. If it finds a supported client
but not its matching instrumentor, it warns with the exact `npm install`
command and leaves the application running normally.

Do not enable an auto-instrumentor and an explicit adapter around the same
call. That records the call twice. In particular, use the LangChain callback
below for LangChain calls; do not add LangChain auto-instrumentation as well.

For a raw OpenAI client in Next.js, add `openai` and
`@traceloop/instrumentation-openai` to `serverExternalPackages`, import the
instrumentor literally inside the Node branch of `instrumentation.ts`, then
`await initAsync({ tracingEnabled: true })`. This makes Next copy the optional
runtime dependency graph into standalone deployments. Use the matching
Anthropic package names for a raw Anthropic client.

### 4a. Vercel AI SDK

```ts
import { wrapAISDK } from "neosigma";
import * as ai from "ai";

const { generateText, streamText } = wrapAISDK(ai);
```

`wrapAISDK(ai)` returns wrapped `generateText`, `streamText`, `generateObject`,
and `streamObject` that enable the AI SDK's telemetry per call and route it
through NeoSigma's own tracer. Use them exactly like the originals; the AI SDK's
telemetry spans nest under the active turn, with token usage on canonical
`gen_ai.usage.*` keys. An explicit `experimental_telemetry` option at a call
site always wins, including `{ isEnabled: false }`.

**Pitfall (ai v7): install `@ai-sdk/otel`.** As of ai v7, span emission moved
into `@ai-sdk/otel`. Install it, or the AI SDK emits no spans and `wrapAISDK`
logs a one-time warning. `wrapAISDK` injects the `@ai-sdk/otel` integration
pointed at NeoSigma's tracer on each call, so under the default private provider
the spans reach NeoSigma without owning the global. Setting
`experimental_telemetry: { isEnabled: true }` by hand, without `wrapAISDK`, does
not reach NeoSigma under the private default.

`wrapAISDK(ai)` may run before or after `init()`. It resolves NeoSigma's tracer
on each call, not at wrap time, so create the wrapped functions once at module
scope and reuse them across requests. You install `@ai-sdk/otel` but do not
import it yourself; `wrapAISDK` loads it.

For `streamText`/`streamObject`, consume the stream inside the `turn()` callback.
`turn()` ends when its callback's promise settles, so returning an undrained
stream closes the turn before the model spans finish. To stream to your client,
write each delta as you consume it inside the callback; do not hand the undrained
stream to a response helper that returns before it finishes, or the turn closes
early.

```ts
import { turn } from "neosigma";

await turn({ turnId, sessionId, userMessage }, async (t) => {
  const result = streamText({ model, prompt: userMessage });
  let output = "";
  for await (const delta of result.textStream) output += delta;
  t.finish({ output });
});
```

If a `streamText`/`streamObject` call runs in the same synchronous tick as
`wrapAISDK()`, `await preloadAISDK()` (exported from `neosigma`) first so that
first stream's spans are captured; later calls self-heal.

### 4b. LangChain

```ts
import { neosigmaCallbackHandler } from "neosigma";
import { turn } from "neosigma";

const handler = neosigmaCallbackHandler(); // reusable across invocations, incl. concurrent

await turn({ sessionId, distinctId, userMessage }, async () => {
  await chain.invoke(input, { callbacks: [handler] });
});
```

`neosigmaCallbackHandler()` returns a LangChain callback handler; pass it in the
`callbacks` array (per call, or on the model/chain constructor). It produces the
run tree (chain > llm > tool) as spans under the active turn, keyed by
LangChain's `runId`/`parentRunId`. One handler instance is safe to reuse and is
concurrency-safe (each run gets a fresh run id). Token usage is read from the
message's `usage_metadata`, so non-OpenAI providers (Anthropic and others) get
token counts, not only OpenAI.

Wrap the LangChain call in a `turn()` so the tree has a root and an id. Without
a surrounding turn the chain spans are orphaned.

The handler emits through NeoSigma's own tracer, so under the default private
provider its spans reach NeoSigma without owning the global.

### 4c. Claude Agent SDK

```ts
import { traceClaude, wrapClaudeQuery } from "neosigma";

for await (const message of traceClaude(query({ prompt }))) { ... }
// Or a reusable drop-in that wraps query():
const tracedQuery = wrapClaudeQuery(query);
```

`traceClaude` wraps the message stream from `query(...)`; iterate the wrapped
stream as usual. It rotates one turn per user message in the stream, with `chat`
and `execute_tool` children.

**Pitfall: a single-shot `query({ prompt })` opens no turn.** Turn rotation
triggers on user-type messages in the stream. A single-shot `query({ prompt })`
emits system, assistant, and result messages but no user message, so
`traceClaude` produces a lone `chat` span with no turn root and no `turnId`.
Wrap it in an explicit `turn()` yourself, or use the streaming-input form that
carries user messages:

```ts
await turn({ sessionId, distinctId, userMessage: prompt }, async () => {
  for await (const message of traceClaude(query({ prompt }))) { ... }
});
```

### 4d. Anthropic Managed Agents

```ts
import { wrapManagedAgents } from "neosigma";
const client = wrapManagedAgents(new Anthropic());
```

Use the wrapped client exactly as before (create session, stream events). The
adapter is a transparent proxy: every other method and property delegates
unchanged, and it emits one turn per user message with `chat`/`execute_tool`
children. No `turn()` needed; the adapter rotates turns itself.

### 4e. Tool functions and manual spans

```ts
import { tool, interaction, turnHandler } from "neosigma";

const search = tool(async (query: string) => db.search(query), { name: "search" });

const handleChat = turnHandler(
  async (req: ChatRequest) => llm.complete(req.message),
  {
    sessionFrom: (args) => (args[0] as ChatRequest).sessionId,
    messageFrom: (args) => (args[0] as ChatRequest).message,
    // outputFrom defaults to String(result), so the return value IS recorded here
  },
);
```

- `tool(fn, { name })` wraps a function as an `execute_tool` span. It sets
  `gen_ai.tool.name` and captures the call arguments and the return value as
  span content, subject to the content capture setting in section 7. Prefer it
  over a manual span for a tool call. `span()` records only what you pass it, so
  a tool traced that way shows its name with no input and no output.
- `interaction(fn, { name })` wraps a function as an `invoke_agent` span but does
  NOT open a turn (no ids). Prefer `turn()` for the request boundary.
- `turnHandler(fn, { sessionFrom, messageFrom, outputFrom })` wraps a handler
  whose arguments carry the ids into a `turn()`. Unlike bare `turn()`, it records
  the return value as output by default.

When no wrapper fits, such as a step inside a function or a call whose
boundary you do not control, open a span directly:

```ts
import { setContent, span } from "neosigma";

const ranked = span("rerank", { input: { query, candidates: 20 } }, (s) => {
  const result = rerank(query, candidates);
  setContent(s, { completion: JSON.stringify(result) });
  return result;
});
```

`span(name, { operation?, attributes?, input? }, fn)` records `input` and
nothing else. The callback's return value is NOT captured, so write the output
yourself with `setContent`. Use `startChat()`/`endChat()` and
`startTool()`/`endTool()` when the open and the close happen in different
functions.

The open and close pairs take typed objects, so the fields must be passed by
name:

- `startChat({ model, promptMessages?, thinkingMode? })` opens a `chat` span.
  `model` is required.
- `endChat(span, { completion?, usage?, latencyMs?, isError?, errorMessage? })`
  closes it. **`completion` is optional and nothing fails when it is missing,
  but the span then records no output.** Pass the model's reply here rather than
  writing it separately.
- `startTool({ name, argsJson? })` opens an `execute_tool` span. `name` is
  required.
- `endTool(span, { result?, isError?, errorMessage? })` closes it.

Two more setters apply to a custom span. `setTokenUsage(span, usage)` attaches
token counts, which `endChat()` takes as an argument instead.
`setCorrelation(span, { turnId?, distinctId?, sessionId?, project? })` stamps
correlation ids onto a span produced outside an active turn, leaving any id you
omit unset rather than blank.

**Pitfall: an inline anonymous arrow gets a generic span name.** `tool()` and
`interaction()` default the span name to the function's `.name`, which is empty
for an inline arrow (`tool(async (q) => ...)` produces a span literally named
`tool`). Pass `{ name }`, or wrap a named `function`.

### 4f. Record where a tool stored a file

Use `artifact()` to record the address of a file a tool produced. NeoSigma
stores the address only. It never reads, uploads, or copies the file, so the
value must be an address your own systems can resolve later. Requires 0.9.0 or
later.

```ts
import { artifact, tool } from "neosigma";

const createPresentation = tool(
  async (topic: string) => {
    const path = `decks/${topic}.pptx`;
    await storage.upload(path, buildDeck(topic));
    artifact(`s3://artifacts/${path}`, `${topic}.pptx`);
    return "Created your deck.";
  },
  { name: "create_presentation" },
);
```

| Parameter | Default | Description |
| --- | --- | --- |
| `location` | Required | Where the artifact lives. Opaque to the SDK, so any address your systems resolve works, such as `s3://bucket/key` or `postgres://public.decks/9f3a`. |
| `name` | `undefined` | Display label for the trace UI. |

`artifact()` attaches to the span that is currently open, so call it inside a
`tool()` function, inside a `turn()`, or inside any active span. Called with no
active span, or before `init()`, it records nothing. Call it once per artifact.
Multiple calls on one span accumulate in call order, up to 100 per span.

Artifacts follow the content capture setting in section 7. When
`captureContent` is `false`, no address is recorded. A location that is empty or
longer than `maxContentChars` is dropped. A name that cannot be recorded is
omitted while the entry keeps its location. Dropped references are logged on the
first drop, then once per 100.

## 5. Existing OpenTelemetry: coexistence and dual export

`init()` needs an OpenTelemetry `TracerProvider` to emit spans. By default it
builds a PRIVATE provider (NeoSigma's own, never registered as the process
global), so NeoSigma runs alongside any existing OpenTelemetry setup (Sentry,
Datadog, LangSmith, `opentelemetry-instrument`) with no change to that tool's
configuration and without capturing that tool's spans. When the app already
configures OpenTelemetry, the default is correct. Call `init()` as usual, with
no flag. There is NO `attach` or `attachToExistingProvider` option.

A private provider isolates NeoSigma's span processing and export from the
host's. Each `turn()` opens its own trace with a fresh trace id (one trace per
turn), so an agent trace is never grafted onto the host's trace tree.
Correlation is by attribute. `turn_id` and `session_id` tie an agent's spans to
your product events and group a conversation's turns, independent of trace ids.

### Dual export: also send to another backend

Dual export sends the spans NeoSigma emits to NeoSigma AND another OpenTelemetry
backend at once. Add the other backend's span processor to the provider NeoSigma
builds, via `extraSpanProcessors`. This works in the default private posture and
in own mode (below). Everything this SDK emits (`turn()`, the wrappers, the
adapters, auto-instrumented clients) then reaches both backends. OpenTelemetry
JS 2.x providers accept span processors only at construction and have no
`addSpanProcessor`, so this is how you add a second backend.

```ts
import { init } from "neosigma";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";

init({
  apiKey: process.env.NEOSIGMA_API_KEY,
  extraSpanProcessors: [
    new BatchSpanProcessor(
      new OTLPTraceExporter({
        url: "https://api.smith.langchain.com/otel/v1/traces", // e.g. LangSmith
        headers: { "x-api-key": process.env.LANGSMITH_API_KEY, "Langsmith-Project": "my-project" },
      }),
    ),
  ],
});
// await shutdown() drains both legs; no separate flush of the extra processor needed.
```

`extraSpanProcessors` ride NeoSigma's provider, so `flush()` and `shutdown()`
cover them too. In the default private posture this exports the agent trace
(what NeoSigma instruments) to both backends. To also hand another backend the
host's non-agent spans, use own mode or the `tracerProvider` handoff below.

### Route everything through one provider you own

To send spans through a single provider you construct, build it with
`CorrelationSpanProcessor` and `NeoSigmaSpanProcessor` alongside your other
backend's processor, and hand it to `init({ tracerProvider })`:

```ts
import {
  init,
  CorrelationSpanProcessor,
  NeoSigmaSpanProcessor,
} from "neosigma";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";

const provider = new NodeTracerProvider({
  spanProcessors: [
    new CorrelationSpanProcessor(),
    new NeoSigmaSpanProcessor(process.env.NEOSIGMA_API_KEY!),
    // your other backend's span processor
  ],
});

init({ tracerProvider: provider });
```

Include BOTH `CorrelationSpanProcessor` and `NeoSigmaSpanProcessor` in
`spanProcessors`. OTel JS 2.x accepts processors only at construction, so
NeoSigma cannot add them to a provider it did not build. If either is missing,
spans are created but never reach NeoSigma. You construct the provider, so its
resource, sampler, and span limits stay whatever you set. Passing
`init({ tracerProvider })` also wires up NeoSigma's native helpers and
product-event sink. Adding `NeoSigmaSpanProcessor` to a provider without calling
`init()` exports spans but does not.

### Own the process global (opt-in)

`init({ privateProvider: false })` (or `NEOSIGMA_PRIVATE_PROVIDER=false`)
registers NeoSigma's provider as the process global and captures every
OpenTelemetry span in the process, including spans from other instrumented
libraries. If another provider already owns the global, NeoSigma falls back to a
private provider and leaves it untouched. Combine with `extraSpanProcessors` to
fan every captured span out to a second backend.

Key facts:

- The OpenTelemetry packages above are NeoSigma dependencies, but strict
  package managers such as pnpm require direct declarations for packages your
  application imports. Add those packages to the application when needed.
- Requires OpenTelemetry JS 2.x, which takes span processors at construction and
  has no `addSpanProcessor`. A second backend rides `extraSpanProcessors` or the
  `tracerProvider` handoff rather than a post-init add.
- Without an API key, and without `NEOSIGMA_CONSOLE_EXPORT=true`, the private and
  own postures install no provider, so every span helper is a no-op. The
  `tracerProvider` handoff is the exception. It emits through the caller's
  provider even without a NeoSigma key, though spans reach NeoSigma only if that
  provider carries `NeoSigmaSpanProcessor`.
- The second backend receives every span exactly as emitted. NeoSigma normalizes
  foreign attribute names (for example the AI SDK's) on its own export leg only,
  using a cloned view, so the other backend is unaffected regardless of which
  exporter flushes first.
- With `extraSpanProcessors`, native `turn()`/`tool()`, the AI SDK, and LangChain
  all fan out to both legs. AI SDK sources still need `@ai-sdk/otel` installed
  (section 4a), or both legs get zero AI SDK spans.

## 6. Concurrency and cross-process continuity

- Correlation rides `AsyncLocalStorage`: ids survive `await` and concurrent
  requests as long as each request has its own `turn()` or `trace()` scope.
  Concurrent turns via `Promise.all` produce distinct traces with no `turnId`
  cross-contamination.
- Ids do NOT cross a process, worker, or queue hop. Put `turnId`/`sessionId` in
  the job payload and re-bind on the far side: open a new `turn()` for a NEW
  user message (same `sessionId`, fresh turn id), or re-bind the SAME `turnId`
  with `trace()` for deferred work on an existing message. Each process runs its
  own `init()`/`shutdown()`.

## 7. Content and privacy

`init({ settings: { captureContent: false } })` (env
`NEOSIGMA_CAPTURE_CONTENT=false`) records metadata only (tokens, tool names,
timings) and drops prompt/completion/tool IO text everywhere, including adapters.
`maxContentChars` (default 24000; `0` disables truncation) truncates captured
text per field and appends a `... [truncated, N chars omitted]` marker.

## 8. Verify

1. Locally, set `NEOSIGMA_CONSOLE_EXPORT=true`, run one real request, and add
   `await flush()` (or `shutdown()`) before the process exits, or the console
   prints nothing. Confirm spans named `invoke_agent` (or `turn`), `chat`, and
   `execute_tool`, each carrying a `neosigma.turn_id` attribute. Console export
   without an API key proves shape, not delivery.
2. With `NEOSIGMA_API_KEY` set, run one request, then check the traces page at
   https://platform.neosigma.ai: one trace per user message; an `invoke_agent` root;
   `chat` children with token usage; `execute_tool` children; and the expected
   `neosigma.session_id`, `neosigma.turn_id`, and `neosigma.distinct_id`.
3. Dual export: confirm the app's original backend still receives the same spans.

Troubleshooting:

| Symptom | Cause and fix |
| --- | --- |
| Nothing in NeoSigma, no errors | No `NEOSIGMA_API_KEY` in that environment, or `NEOSIGMA_ENABLED=false`. The SDK is silent by design; set the key. |
| Console export prints nothing on a short script | The process exited before the 5s batch flush. Add `await flush()` or `await shutdown()` before exit (section 2). |
| AI SDK calls produce zero spans | On ai v7, `@ai-sdk/otel` is not installed. Install it; `wrapAISDK` injects it per call (section 4a). |
| Claude single-shot `query({ prompt })` has a `chat` span but no turn | A single-shot query emits no user message, so no turn opens. Wrap it in an explicit `turn()` (section 4c). |
| Span named `tool` / `interaction` instead of the function name | An inline anonymous arrow has no `.name`. Pass `{ name }` (section 4e). |
| `init({ tracingEnabled: true })` logs that an instrumentor is missing | Install the matching `@traceloop/instrumentation-*` package, or use the section 4 adapter. |
| Exports 404 | `otelEndpoint` was set to a base URL. It must be the full path ending `/v1/traces` (section 2). |
| Ingest returns 400 | A JSON OTLP exporter was used. Use `@opentelemetry/exporter-trace-otlp-proto` (protobuf). |
| NeoSigma not capturing another tool's spans | Working as intended. The default provider is private, so NeoSigma traces only what it instruments, not the host's other spans. To capture every span in the process, own the global with `init({ privateProvider: false })`. To also export NeoSigma's spans to that tool, add its processor via `extraSpanProcessors` (section 5). |
| Flat traces / spans missing a parent | The work is not inside an active turn (process/queue hop, or no `turn()`). Re-bind ids (section 6). |
| Spans stop partway through a run | Process exited without flushing. Wire the section 2 lifecycle for the runtime shape. |

## Mirror analytics events (optional)

If the app already sends PostHog or Mixpanel events, `wrapPosthog(client)` /
`wrapMixpanel(client)` return transparent proxies that mirror each event to
NeoSigma as a product event joined to the active turn, while the original
provider still receives it. This is product-event territory; see
[typescript-events.md](typescript-events.md) for `capture`/`identify` and the
turn-binding rules.
