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

# Generate self-signed cert for HTTPS (for Cloudflare Full/Strict mode)
if [ ! -f /app/backend/data/server.crt ]; then
    echo "[startup] Generating self-signed SSL certificate..."
    openssl req -x509 -newkey rsa:2048 -keyout /app/backend/data/server.key \
        -out /app/backend/data/server.crt -days 3650 -nodes \
        -subj "/CN=${SERVER_HOST:-ads.lhwebs.com}" 2>/dev/null
fi

# Start lightweight HTTPS → HTTP proxy on port 443 (Python built-in, no extra deps)
python3 -c "
import ssl, socket, select, threading, sys
CERT, KEY = '/app/backend/data/server.crt', '/app/backend/data/server.key'
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('0.0.0.0', 443))
srv.listen(128)
print('[ssl-proxy] Listening on 0.0.0.0:443 → localhost:8011')
def handle(conn):
    try:
        with ctx.wrap_socket(conn, server_side=True) as ss:
            data = ss.recv(65536)
            if not data: return
            with socket.create_connection(('127.0.0.1', 8011), timeout=30) as up:
                up.sendall(data)
                up.shutdown(socket.SHUT_WR)
                while True:
                    chunk = up.recv(65536)
                    if not chunk: break
                    try: ss.sendall(chunk)
                    except: break
    except: pass
    finally: conn.close()
while True:
    conn, _ = srv.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
" &

exec gunicorn \
    --bind "${WP_HOST:-0.0.0.0}:${WP_PORT:-8011}" \
    --workers "${WP_WORKERS:-4}" \
    --worker-class gevent \
    --timeout 120 \
    --access-logfile /app/logs/access.log \
    --error-logfile /app/logs/error.log \
    --chdir /app/backend \
    "app:create_app()"
