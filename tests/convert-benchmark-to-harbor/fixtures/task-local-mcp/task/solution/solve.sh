#!/bin/sh
# Reference run: call the task's MCP server over stdio, as the agent would.
set -eu
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"oracle","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"lookup_price","arguments":{"sku":"A-17"}}}' \
  | python3 /opt/tools/catalog_mcp.py \
  | python3 -c 'import json,sys; replies=[json.loads(l) for l in sys.stdin]; print(replies[-1]["result"]["content"][0]["text"])' \
  > /app/price.txt
