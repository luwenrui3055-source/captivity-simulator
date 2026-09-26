#!/bin/sh
set -e
export PYTHONIOENCODING=utf-8
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
if [ -n "$BASIC_AUTH_USER" ] && [ -n "$BASIC_AUTH_PASS" ]; then
    htpasswd -cb /app/.htpasswd "$BASIC_AUTH_USER" "$BASIC_AUTH_PASS"
    export CAPTIVITY_BASIC_AUTH_FILE=/app/.htpasswd
fi
export CAPTIVITY_HOST=0.0.0.0
export CAPTIVITY_PORT=5058
echo "starting flask"
captivity-simulator &
echo "starting supergateway"
if [ -n "$MCP_TOKEN" ]; then
    exec supergateway --stdio "python -u -m captivity_simulator.mcp_server" --port 8765 --ssePath /sse --messagePath /message --header "X-MCP-Token: $MCP_TOKEN" --cors
else
    exec supergateway --stdio "python -u -m captivity_simulator.mcp_server" --port 8765 --ssePath /sse --messagePath /message --cors
fi
