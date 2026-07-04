#!/bin/bash
# Copy default plugins if volume is empty
for f in /app/backend/plugins-default/*.zip; do
    [ -f "$f" ] || continue
    dst="/app/backend/plugins/$(basename "$f")"
    [ -f "$dst" ] || cp "$f" "$dst"
done

# Copy default themes if volume is empty
for f in /app/backend/themes-default/*.zip; do
    [ -f "$f" ] || continue
    dst="/app/backend/themes/$(basename "$f")"
    [ -f "$dst" ] || cp "$f" "$dst"
done

# Start virtual X server for headed browser automation (GMC recon etc.)
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR &
export DISPLAY=:99

# Wait for Xvfb to be ready
sleep 1

# Start x11vnc sharing the Xvfb display (no password, local-only)
x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 &

# Start websockify to bridge VNC → WebSocket for noVNC
websockify --web /opt/novnc 0.0.0.0:6080 localhost:5900 &

# Pre-install CloakBrowser (Playwright) Chromium browser if not cached
if [ ! -d "${PLAYWRIGHT_BROWSERS_PATH}/chromium-"* ] 2>/dev/null || [ -z "$(ls -A "${PLAYWRIGHT_BROWSERS_PATH}" 2>/dev/null)" ]; then
    echo "[startup] Installing Chromium browser for CloakBrowser (one-time)..."
    python -m playwright install chromium
else
    echo "[startup] Chromium browser already cached, skipping install."
fi

exec gunicorn \
    --bind "${WP_HOST:-0.0.0.0}:${WP_PORT:-8011}" \
    --workers "${WP_WORKERS:-4}" \
    --worker-class gevent \
    --timeout 120 \
    --backlog "${WP_BACKLOG:-2048}" \
    --keep-alive "${WP_KEEPALIVE:-5}" \
    --graceful-timeout "${WP_GRACEFUL_TIMEOUT:-30}" \
    --access-logfile /app/logs/access.log \
    --error-logfile /app/logs/error.log \
    --chdir /app/backend \
    "app:create_app()"
