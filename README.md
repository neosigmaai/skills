# NeoSigma Skills

[Agent Skills](https://github.com/anthropics/skills) that teach AI coding assistants how to work with [NeoSigma](https://neosigma.ai): instrument a codebase with the NeoSigma SDK, import existing checks as verifiers, and evaluate Clay on GTM Bench.

Full SDK documentation: [docs.neosigma.ai](https://docs.neosigma.ai)

## Skills

| Skill                                   | Description                                                                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [integrate-sdk](./skills/integrate-sdk) | Integrate the NeoSigma SDK: agent tracing (Python and TypeScript), product events (Python and TypeScript), framework adapters (Vercel AI SDK, LangChain, Claude Agent SDK, Managed Agents), and dual export with an existing OpenTelemetry setup, correlated per turn. |
| [import-verifiers](./skills/import-verifiers) | Translate a codebase's trace-evaluable quality checks into repository-backed verifier YAML, then apply the complete set through the NeoSigma MCP sync workflow. |
| [clay-plugin-eval](./skills/clay-plugin-eval) | Set up Clay credentials in a NeoSigma Vault, run the fixed GTM Bench dataset with and without the Clay plugin, and generate a comparison report. |
| [convert-benchmark-to-harbor](./skills/convert-benchmark-to-harbor) | Convert a benchmark in the developer's own workspace into faithful Harbor tasks, validate each task, and publish immutable task files to a NeoSigma dataset. |

## Installation

### Use your coding agent

Use your coding agent with this instruction so it can install the NeoSigma
skill and apply it to your task.

```txt
Install the integrate-sdk skill from github.com/neosigmaai/skills
and use it to add NeoSigma tracing to this application
following NeoSigma best practices.
```

### Cursor

Install as a [Cursor plugin](https://cursor.com/docs/plugins):

```
/add-plugin neosigma
```

Or via the skills CLI:

```bash
npx skills add neosigmaai/skills --skill "integrate-sdk" --agent cursor
```

### Claude Code

Add the marketplace and install:

```bash
claude plugin marketplace add neosigmaai/skills
claude plugin install neosigma@neosigma-skills
```

Or via the skills CLI:

```bash
npx skills add neosigmaai/skills --skill "integrate-sdk" --agent claude-code
```

### Install with npx

```bash
npx skills add neosigmaai/skills --skill "integrate-sdk"
```

## Prerequisites

Set your NeoSigma API key before asking an agent to instrument your codebase. Generate an `ns_live_...` key in Settings > Developer > API Keys.

```bash
export NEOSIGMA_API_KEY=...
```

Without a key the SDK is a no-op, so instrumentation is safe to merge before keys are provisioned.

## Usage

Once installed, your agent can use these skills when you ask it to:

- Add NeoSigma tracing to a Python or TypeScript/Node agent or workflow
- Trace the Vercel AI SDK or LangChain, the Claude Agent SDK, or Anthropic Managed Agents
- Send product events from a TypeScript/Node app, correlated to the agent trace by turn
- Add a FastAPI/Starlette middleware that opens a turn per request
- Dual-export to NeoSigma alongside an existing OpenTelemetry backend
- Verify that traces and events reached NeoSigma
- Import existing trace-evaluable quality checks as repository-backed verifier YAML
- Evaluate the Clay plugin against the fixed GTM Bench dataset
- Convert a benchmark in this workspace into Harbor tasks and publish a NeoSigma dataset
