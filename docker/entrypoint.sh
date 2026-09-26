#!/bin/sh
set -e

# ==== Basic Auth（网页服务用） ====
if [ -n "$BASIC_AUTH_USER" ] && [ -n "$BASIC_AUTH_PASS" ]; then
    htpasswd -cb /app/.htpasswd "$BASIC_AUTH_USER" "$BASIC_AUTH_PASS"
    echo "[entrypoint] Basic Auth enabled for user: $BASIC_AUTH_USER"
    export CAPTIVITY_BASIC_AUTH_FILE=/app/.htpasswd
else
    echo "[entrypoint] Basic Auth disabled (BASIC_AUTH_USER/PASS not set)"
fi

# ==== 网页服务监听配置 ====
export CAPTIVITY_HOST=0.0.0.0
export CAPTIVITY_PORT=5058

# ==== MCP Token（SSE URL鉴权） ====
if [ -z "$MCP_TOKEN" ]; then
    echo "[entrypoint] WARNING: MCP_TOKEN not set, SSE endpoint has NO auth!"
fi

# ==== 后台启动Flask网页服务 ====
echo "[entrypoint] Starting Flask web server on 0.0.0.0:5058 ..."
captivity-simulator &
FLASK_PID=$!

# ==== 前台启动supergateway包装MCP stdio → SSE ====
# 通过 URL 参数鉴权：客户端连接时必须带 ?token=<MCP_TOKEN>
echo "[entrypoint] Starting supergateway MCP SSE on 0.0.0.0:8765 ..."
if [ -n "$MCP_TOKEN" ]; then
    exec npx -y supergateway \
        --stdio "python -m captivity_simulator.mcp_server" \
        --port 8765 \
        --ssePath "/sse" \
        --messagePath "/message" \
        --header "X-MCP-Token: $MCP_TOKEN" \
        --cors
else
    exec npx -y supergateway \
        --stdio "python -m captivity_simulator.mcp_server" \
        --port 8765 \
        --ssePath "/sse" \
        --messagePath "/message" \
        --cors
fi
