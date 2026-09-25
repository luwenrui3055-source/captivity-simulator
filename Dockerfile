# ==== Stage 1: 构建前端 ====
FROM node:20-alpine AS web-builder

WORKDIR /build

# 先复制配置文件（构建时会读 config/local.json）
COPY config/ ./config/

# 复制前端源码
COPY web/ ./web/

# 构建前端
WORKDIR /build/web
RUN npm install
RUN npm run build

# ==== Stage 2: Python运行时 ====
FROM python:3.11-slim

# 装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制Python项目
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config/ ./config/

# 从stage 1复制构建好的前端
COPY --from=web-builder /build/web/dist ./web/dist

# 装Python依赖 + passlib（用于Basic Auth）
RUN pip install --no-cache-dir -e . "passlib[bcrypt]"

# 创建data目录（Volume挂载点）
RUN mkdir -p /app/data/saves

# 复制启动脚本和robots.txt
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/robots.txt /app/web/dist/robots.txt
RUN chmod +x /entrypoint.sh

EXPOSE 5058

ENV PYTHONUNBUFFERED=1
ENV CAPTIVITY_DATA_DIR=/app/data

ENTRYPOINT ["/entrypoint.sh"]

