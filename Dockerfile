# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
RUN pip config set global.index-url https://pypi.org/simple/
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl iproute2 ca-certificates gnupg \
        libglib2.0-0 libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
        libcups2t64 libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
        libasound2t64 libexpat1 libx11-6 libxcb1 libxfixes3 libxext6 \
        fontconfig fonts-liberation xvfb x11vnc && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    mkdir -p /app/logoloom && cd /app/logoloom && npm init -y > /dev/null 2>&1 && \
    npm install @mcpware/logoloom zod && \
    apt-get purge -y gnupg && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* /root/.npm /root/.cache

COPY --from=builder /install /usr/local
WORKDIR /app
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY requirements.txt .
COPY backend/start.sh /start.sh

RUN curl -fsSL https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz | \
    tar xz -C /opt && \
    mv /opt/noVNC-1.5.0 /opt/novnc

RUN mkdir -p /app/backend/data /app/backend/plugins /app/backend/themes /app/backend/brand-kits /app/backend/browsers /app/logs && \
    chmod +x /start.sh

RUN cp -r /app/backend/plugins /app/backend/plugins-default && \
    cp -r /app/backend/themes /app/backend/themes-default

ENV PLAYWRIGHT_BROWSERS_PATH=/app/backend/browsers

EXPOSE 8011
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8011/ || exit 1

CMD ["/start.sh"]
