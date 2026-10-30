# NeoSigma Skills

The NeoSigma plugin for AI coding agents. It connects your agent to the [NeoSigma](https://neosigma.ai) MCP server and adds [Agent Skills](https://github.com/anthropics/skills) that teach the agent how to work with NeoSigma: instrument a codebase with the NeoSigma SDK, import existing checks as verifiers, suggest verifiers that catch missed production failures, convert benchmarks into Harbor tasks, evaluate Clay on GTM Bench, and set up a workspace's sandbox.

Full SDK documentation: [docs.neosigma.ai](https://docs.neosigma.ai)

## Skills

| Skill                                   | Description                                                                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [integrate-sdk](./skills/integrate-sdk) | Integrate the NeoSigma SDK: agent tracing (Python and TypeScript), product events (Python and TypeScript), framework adapters (Vercel AI SDK, LangChain, Claude Agent SDK, Managed Agents), and dual export with an existing OpenTelemetry setup, correlated per turn. |
| [import-verifiers](./skills/import-verifiers) | Translate a codebase's trace-evaluable quality checks into repository-backed verifier YAML, then apply the complete set through the NeoSigma MCP sync workflow. |
| [clay-plugin-eval](./skills/clay-plugin-eval) | Set up Clay credentials in a NeoSigma Vault, run the fixed GTM Bench dataset with and without the Clay plugin, and generate a comparison report. |
| [convert-benchmark-to-harbor](./skills/convert-benchmark-to-harbor) | Convert a benchmark in the developer's own workspace into faithful Harbor tasks, validate each task, and publish immutable task files to a NeoSigma dataset. |
| [setup-environment](./skills/setup-environment) | Write the setup script that boots a workspace's repositories in a NeoSigma sandbox into the repository, verify it on a branch, repair it from the verification diagnostics until it passes, and open a pull request. |
| [suggest-verifiers-from-failures](./skills/suggest-verifiers-from-failures) | Find a project's recent failures that no verifier caught, and suggest a verifier change or a new verifier that catches them, for review on the Verifiers page. |

## What the plugin includes

- **NeoSigma MCP server** (`https://api.neosigma.ai/mcp`): tools to read workspaces, projects, traces, issues, datasets, evaluation runs, and verifiers, to start evaluation runs, and to read and verify a workspace's setup script.
- **Skills**: the skills in the table above.

## Sign-in

Claude Code and Codex sign you in to the MCP server with your NeoSigma account, in the browser, the first time you connect. You do not copy an API key. The agent acts in the organization you opened last in NeoSigma. To use another organization, switch to it in NeoSigma.

## Installation

### Claude Code

Run these commands inside Claude Code:

```
/plugin marketplace add neosigmaai/skills
/plugin install neosigma@neosigma-skills
```

Then run `/mcp`, select `neosigma`, and select **Authenticate**. Sign in to NeoSigma in the browser that opens. Claude Code keeps the sign-in in secure storage and renews it.

To make sure that the plugin works, run `/mcp` and look for `neosigma`.

### Codex

1. Add the marketplace:

   ```bash
   codex plugin marketplace add neosigmaai/skills
   ```

2. Start Codex, run `/plugins`, and install **neosigma**.

3. Sign in to NeoSigma in the browser that this command opens:

   ```bash
   codex mcp login neosigma
   ```

To make sure that the plugin works, run `/mcp` and look for `neosigma`.

### Pi

Requires Pi 0.99.0 or later, which added built-in MCP support. Check with `pi --version`.

1. Create an `ns_live_...` API key in NeoSigma under **Settings > Developer > API Keys**, and set the `NEOSIGMA_API_KEY` environment variable to it.

   macOS and Linux:

   ```bash
   export NEOSIGMA_API_KEY=ns_live_...
   ```

   Windows (PowerShell). The variable is available only in new terminal windows:

   ```powershell
   setx NEOSIGMA_API_KEY "ns_live_..."
   ```

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

The SDK reads a NeoSigma API key from `NEOSIGMA_API_KEY`. Create an `ns_live_...` key in NeoSigma under **Settings > Developer > API Keys**:

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
- Find yesterday's failures that no verifier caught, and suggest verifiers that catch them
- Evaluate the Clay plugin against the fixed GTM Bench dataset
- Convert a benchmark in this workspace into Harbor tasks and publish a NeoSigma dataset
- Write the setup script that boots this repository in a NeoSigma sandbox, verify it, and open a pull request
