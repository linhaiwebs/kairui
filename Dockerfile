# ============================================================
# WordPress Site Manager - Dockerfile
# Multi-stage build for minimal image size
# ============================================================

# ---- Stage 1: Build ----
FROM python:3.12-slim AS builder

WORKDIR /build

# Install dependencies to a separate folder
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.12-slim

LABEL maintainer="WP Site Manager"
LABEL description="WordPress Site Manager - 1Panel集成管理平台"

# Install runtime deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ss && \
    rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy project files
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY requirements.txt .

# Create necessary directories
RUN mkdir -p /app/backend/plugins /app/logs && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Environment defaults
ENV WP_HOST=0.0.0.0
ENV WP_PORT=8011
ENV WP_WORKERS=4
ENV FLASK_ENV=production

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
