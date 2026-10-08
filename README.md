# NeoSigma Skills

The NeoSigma plugin for AI coding agents. It connects your agent to the [NeoSigma](https://neosigma.ai) MCP server and adds [Agent Skills](https://github.com/anthropics/skills) that teach the agent how to work with NeoSigma: instrument a codebase with the NeoSigma SDK, import existing checks as verifiers, convert benchmarks into Harbor tasks, evaluate Clay on GTM Bench, and analyze recorded agent sessions.

Full SDK documentation: [docs.neosigma.ai](https://docs.neosigma.ai)

## Skills

| Skill                                   | Description                                                                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [integrate-sdk](./skills/integrate-sdk) | Integrate the NeoSigma SDK: agent tracing (Python and TypeScript), product events (Python and TypeScript), framework adapters (Vercel AI SDK, LangChain, Claude Agent SDK, Managed Agents), and dual export with an existing OpenTelemetry setup, correlated per turn. |
| [import-verifiers](./skills/import-verifiers) | Translate a codebase's trace-evaluable quality checks into repository-backed verifier YAML, then apply the complete set through the NeoSigma MCP sync workflow. |
| [clay-plugin-eval](./skills/clay-plugin-eval) | Set up Clay credentials in a NeoSigma Vault, run the fixed GTM Bench dataset with and without the Clay plugin, and generate a comparison report. |
| [convert-benchmark-to-harbor](./skills/convert-benchmark-to-harbor) | Convert a benchmark in the developer's own workspace into faithful Harbor tasks, validate each task, and publish immutable task files to a NeoSigma dataset. |
| [analyze-sessions](./skills/analyze-sessions) | Search a project's recorded agent sessions, open the matches, read their turns, diagnoses, and traces, and report what happened with trace links. |

## What the plugin includes

- **NeoSigma MCP server** (`https://api.neosigma.ai/mcp`): tools to read workspaces, projects, sessions, traces, issues, datasets, evaluation runs, and verifiers, and to start evaluation runs.
- **Skills**: the skills in the table above.

## API key

The MCP server needs a NeoSigma API key. Create an `ns_live_...` key in NeoSigma under **Settings > Developer > API Keys**.

## Installation

### Claude Code

Run these commands inside Claude Code:

```
/plugin marketplace add neosigmaai/skills
/plugin install neosigma@neosigma-skills
```

Enter your API key when Claude Code asks for it. Claude Code keeps the key in secure storage.

If you install from a terminal with `claude plugin install`, Claude Code does not ask for the key. Set it inside Claude Code:

```
/plugin configure neosigma@neosigma-skills
```

To make sure that the plugin works, run `/mcp` and look for `neosigma`.

### Codex

1. Set the `NEOSIGMA_API_KEY` environment variable to your API key.

   macOS and Linux:

   ```bash
   export NEOSIGMA_API_KEY=ns_live_...
   ```

   Windows (PowerShell). The variable is available only in new terminal windows:

   ```powershell
   setx NEOSIGMA_API_KEY "ns_live_..."
   ```

2. Add the marketplace:

   ```bash
   codex plugin marketplace add neosigmaai/skills
   ```

3. Start Codex, run `/plugins`, and install **neosigma**.

If you set or change the key while Codex runs, fully stop Codex before you start it again. Codex keeps a background process that does not see new environment variables.

To make sure that the plugin works, run `/mcp` and look for `neosigma`.

### Pi

Requires Pi 0.99.0 or later, which added built-in MCP support. Check with `pi --version`.

1. Set the `NEOSIGMA_API_KEY` environment variable to your API key, as in the Codex steps above.

2. Install the skills:

   ```bash
   pi install git:github.com/neosigmaai/skills
   ```

3. Add the NeoSigma MCP server:

   ```bash
   pi mcp add neosigma --url https://api.neosigma.ai/mcp --bearer-token-env-var NEOSIGMA_API_KEY --exposure direct
   ```

   Pi saves the server in `~/.pi/agent/mcp.json` and reads the key from `NEOSIGMA_API_KEY` each time it connects, so the key is not written to disk.

4. Start Pi. If Pi is already running, run `/reload`.

To make sure that the plugin works, run `/mcp` and look for `neosigma`. If you use `pi-mcp-adapter` or another MCP extension, it replaces Pi's built-in MCP support; remove it to use this setup.

### Cursor

Install as a [Cursor plugin](https://cursor.com/docs/plugins):

```
/add-plugin neosigma
```

The skills work in Cursor. The NeoSigma MCP server does not authenticate in Cursor yet. To use the MCP tools in Cursor, add the server to your Cursor MCP settings with the header `Authorization: Bearer <your API key>`.

### Skills only

To install one skill without the MCP server, use the skills CLI:

```bash
npx skills add neosigmaai/skills --skill "integrate-sdk"
```

Add `--agent cursor` or `--agent claude-code` to select the agent.

### Use your coding agent

You can also tell your coding agent to install a skill and use it:

```txt
Install the integrate-sdk skill from github.com/neosigmaai/skills
and use it to add NeoSigma tracing to this application
following NeoSigma best practices.
```

## SDK prerequisites

The SDK reads the same API key from `NEOSIGMA_API_KEY`:

```bash
export NEOSIGMA_API_KEY=ns_live_...
```

Without a key the SDK is a no-op, so instrumentation is safe to merge before keys are provisioned.

## Usage

Once installed, your agent can use the MCP tools and skills when you ask it to:

- Show the open issues in a NeoSigma workspace, or read a trace

- Add NeoSigma tracing to a Python or TypeScript/Node agent or workflow
- Trace the Vercel AI SDK or LangChain, the Claude Agent SDK, or Anthropic Managed Agents
- Send product events from a TypeScript/Node app, correlated to the agent trace by turn
- Add a FastAPI/Starlette middleware that opens a turn per request
- Dual-export to NeoSigma alongside an existing OpenTelemetry backend
- Verify that traces and events reached NeoSigma
- Import existing trace-evaluable quality checks as repository-backed verifier YAML
- Evaluate the Clay plugin against the fixed GTM Bench dataset
- Convert a benchmark in this workspace into Harbor tasks and publish a NeoSigma dataset
