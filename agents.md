# Agent Instructions

## NeoSigma Skill Path Changes

When changing the path to any NeoSigma skill in this repo, update any install docs or plugin metadata that point to the old location. The expected skill paths are `skills/integrate-sdk`, `skills/import-verifiers`, `skills/clay-plugin-eval`, and `skills/convert-benchmark-to-harbor`.

## Plugin Versions

When you change the plugin, increase the version in `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, and `package.json`. Claude Code and Codex do not update an installed plugin until its version changes.

## MCP Server Configuration

- Claude Code reads `.mcp.json` and gets the API key from `userConfig` in `.claude-plugin/plugin.json`.
- Codex reads `mcpServers` in `.codex-plugin/plugin.json` and gets the API key from the `NEOSIGMA_API_KEY` environment variable.
- Pi reads the `pi` key in the root `package.json`. The extension in `.pi-plugin/neosigma.ts` registers the MCP server with `pi.registerMcpServer()` and gets the API key from the `NEOSIGMA_API_KEY` environment variable. It needs Pi 0.99.0 or later.
- Do not add a root `plugin.json` or `mcp.json` (Agent Plugins format). Codex prefers that format, and it removes the `Authorization` header from plugin MCP servers.
