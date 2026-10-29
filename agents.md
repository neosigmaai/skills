# Agent Instructions

## NeoSigma Skill Path Changes

When changing the path to any NeoSigma skill in this repo, update any install docs or plugin metadata that point to the old location. The expected skill paths are `skills/integrate-sdk`, `skills/import-verifiers`, `skills/clay-plugin-eval`, `skills/convert-benchmark-to-harbor`, `skills/setup-environment`, and `skills/create-evals-from-failures`.

## Plugin Versions

When you change the plugin, increase the version in `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, and `.codex-plugin/plugin.json`. Claude Code and Codex do not update an installed plugin until its version changes.

## MCP Server Configuration

- Claude Code reads `.mcp.json`, and Codex reads `mcpServers` in `.codex-plugin/plugin.json`. Neither sends a credential: the server answers 401, and the agent signs the person in to NeoSigma through Auth0 (OAuth). An `Authorization` header in either file stops that sign-in.
- Do not add a root `plugin.json` or `mcp.json` (Agent Plugins format). Codex prefers that format, and it removes the `Authorization` header from plugin MCP servers.
