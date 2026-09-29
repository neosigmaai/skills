import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Pi's built-in MCP support (0.99.0+) connects this server when a session starts.
// The header reads the key from the environment when Pi connects, so the key is
// never written to disk. Codex reads the same variable.
const API_KEY_ENV = "NEOSIGMA_API_KEY";

export default function neosigma(pi: ExtensionAPI) {
	pi.registerMcpServer("neosigma", {
		url: "https://api.neosigma.ai/mcp",
		headers: { Authorization: `Bearer \${${API_KEY_ENV}}` },
		exposure: "direct",
	});

	pi.on("session_start", async (_event, ctx) => {
		if (process.env[API_KEY_ENV]) return;
		ctx.ui.notify(
			`NeoSigma MCP needs ${API_KEY_ENV}. Create an ns_live_ key in NeoSigma under Settings > Developer > API Keys, set ${API_KEY_ENV}, then restart Pi.`,
			"warning",
		);
	});
}
