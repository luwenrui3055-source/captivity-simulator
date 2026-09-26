# ==== Stage 1: 构建前端 ====
FROM node:20-alpine AS web-builder

WORKDIR /build

COPY config/ ./config/
COPY web/ ./web/

WORKDIR /build/web
RUN npm install
RUN npm run build

# ==== Stage 2: Python运行时 + Node（用于supergateway） ====
FROM python:3.11-slim

# 装系统依赖 + Node.js 20 + apache2-utils
RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2-utils \
    curl \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && npm install -g supergateway \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制Python项目
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config/ ./config/

# 从stage 1复制构建好的前端
COPY --from=web-builder /build/web/dist ./web/dist

# 装Python依赖 + passlib
RUN pip install --no-cache-dir -e . "passlib[bcrypt]"

# 创建data目录（Volume挂载点）
RUN mkdir -p /app/data/saves

# 复制启动脚本和robots.txt
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/robots.txt /app/web/dist/robots.txt
RUN chmod +x /entrypoint.sh

# 5058：网页服务  8765：MCP SSE
EXPOSE 5058 8765

ENV PYTHONUNBUFFERED=1
ENV CAPTIVITY_DATA_DIR=/app/data

ENTRYPOINT ["/entrypoint.sh"]
