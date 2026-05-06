# ============================================================
# WordPress Site Manager - Dockerfile
# Multi-stage build for minimal image size
# ============================================================

# ---- Stage 1: Build ----
FROM python:3.12-slim AS builder

WORKDIR /build

# Install dependencies to a separate folder (use mirror for faster download)
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.12-slim

LABEL maintainer="WP Site Manager"
LABEL description="WordPress Site Manager - 1Panel集成管理平台"

# Install runtime deps only
# Use mirror to avoid Debian repo 502 errors
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && \
    apt-get install -y --no-install-recommends curl iproute2 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy project files
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY requirements.txt .

# Create necessary directories
# data/ is for the persistent DB volume, plugins/ for uploaded plugin files, themes/ for theme files
RUN mkdir -p /app/backend/data /app/backend/plugins /app/backend/themes /app/logs

# Environment defaults
ENV WP_HOST=0.0.0.0
ENV WP_PORT=8011
ENV WP_WORKERS=4
ENV FLASK_ENV=production
# Data directory — in Docker, this is a persistent volume
ENV WP_DATA_DIR=/app/backend/data

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${WP_PORT}/ || exit 1

EXPOSE ${WP_PORT}

# Start with gunicorn
CMD ["sh", "-c", "gunicorn \
    --bind ${WP_HOST}:${WP_PORT} \
    --workers ${WP_WORKERS} \
    --worker-class gevent \
    --timeout 120 \
    --access-logfile /app/logs/access.log \
    --error-logfile /app/logs/error.log \
    --chdir /app/backend \
    'app:create_app()'"]
