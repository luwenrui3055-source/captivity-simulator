#!/bin/sh
set -e

# 如果配了BASIC_AUTH_USER和BASIC_AUTH_PASS，生成htpasswd文件
if [ -n "$BASIC_AUTH_USER" ] && [ -n "$BASIC_AUTH_PASS" ]; then
    htpasswd -cb /app/.htpasswd "$BASIC_AUTH_USER" "$BASIC_AUTH_PASS"
    echo "[entrypoint] Basic Auth enabled for user: $BASIC_AUTH_USER"
    export CAPTIVITY_BASIC_AUTH_FILE=/app/.htpasswd
else
    echo "[entrypoint] WARNING: BASIC_AUTH_USER/PASS not set, running without auth"
fi

# 强制容器监听0.0.0.0（Zeabur要求）
export CAPTIVITY_HOST=0.0.0.0
export CAPTIVITY_PORT=5058

# 启动主服务
exec captivity-simulator
