"""Minimal stdio MCP server exposing one catalog lookup tool."""

import json
import sys

PRICES = {"A-17": "12.40", "B-02": "3.15"}
TOOL = {
    "name": "lookup_price",
    "description": "Return the price of one catalog SKU.",
    "inputSchema": {
        "type": "object",
        "properties": {"sku": {"type": "string"}},
        "required": ["sku"],
    },
}


def respond(request):
    method = request.get("method")
    if method == "initialize":
        return {
            "protocolVersion": request["params"].get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "catalog", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        sku = request["params"]["arguments"]["sku"]
        price = PRICES.get(sku, "unknown SKU")
        return {"content": [{"type": "text", "text": price}]}
    return None


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    result = respond(request)
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    if result is None:
        reply["error"] = {"code": -32601, "message": "method not found"}
    else:
        reply["result"] = result
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
