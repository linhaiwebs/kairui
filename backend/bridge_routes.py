# Kairui 凯瑞指纹 Bridge API
# Imported by routes.py — provides endpoints for the 凯瑞指纹 desktop app
# to list operators and import brand kits as browser profiles.
# Auth: X-API-Key header (from KAIURI_BRIDGE_API_KEY env var, default "kairui-bridge-default-key")

import json
import os as _os
from flask import request, jsonify


def _bridge_api_key():
    return _os.environ.get("KAIURI_BRIDGE_API_KEY", "kairui-bridge-default-key")


def _bridge_auth():
    api_key = request.headers.get("X-API-Key", "")
    if api_key != _bridge_api_key():
        return False, jsonify({"code": 401, "message": "unauthorized: invalid API key"}), 401
    return True, None


def _read_cloakbrowser_config(profile_name):
    """Read a CloakBrowser profile's config.json. Returns dict or None."""
    from services.mc_auto_register import get_profiles_root
    import os
    cfg_path = os.path.join(get_profiles_root(), profile_name, "config.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def register_bridge_routes(app):
    """Register 凯瑞指纹 bridge API routes on the Flask app."""

    @app.route("/api/bridge/ping", methods=["GET"])
    def bridge_ping():
        ok, err = _bridge_auth()
        if not ok:
            return err
        return jsonify({"code": 200, "server": "kairui", "version": "1.0"})

    @app.route("/api/bridge/operators", methods=["GET"])
    def bridge_operators():
        ok, err = _bridge_auth()
        if not ok:
            return err
        from models import get_db
        db = get_db()
        rows = db.execute(
            "SELECT u.id, u.username, u.role, pe.name as panel_name, pe.host as panel_host "
            "FROM users u "
            "LEFT JOIN panel_environments pe ON u.panel_environment_id = pe.id "
            "WHERE u.role = 'operator' ORDER BY u.id"
        ).fetchall()
        return jsonify({
            "code": 200,
            "operators": [
                {
                    "id": r["id"],
                    "username": r["username"],
                    "role": r["role"],
                    "panel_name": r["panel_name"],
                    "panel_host": r["panel_host"],
                }
                for r in rows
            ]
        })

    @app.route("/api/bridge/brand-kits", methods=["GET"])
    def bridge_brand_kits():
        ok, err = _bridge_auth()
        if not ok:
            return err
        operator_id = request.args.get("operator_id", type=int)
        if not operator_id:
            return jsonify({"code": 400, "message": "operator_id is required"}), 400
        from models import get_db
        db = get_db()
        kits = db.execute(
            "SELECT bk.id, bk.name, bk.brand_name, bk.industry, bk.cloakbrowser_profile_name, "
            "p.proxy_url, p.proxy_type "
            "FROM brand_kits bk "
            "LEFT JOIN proxies p ON bk.proxy_id = p.id "
            "WHERE bk.created_by = ? ORDER BY bk.id",
            (operator_id,)
        ).fetchall()

        results = []
        for k in kits:
            proxy_url = None
            proxy_type = None
            fingerprint = None

            # 1. Primary: read from CloakBrowser profile config.json
            profile_name = k["cloakbrowser_profile_name"]
            if profile_name:
                cb_config = _read_cloakbrowser_config(profile_name)
                if cb_config:
                    proxy_url = cb_config.get("proxy", "") or None
                    if proxy_url:
                        proxy_type = "socks5" if proxy_url.startswith("socks5") else "http"
                    fingerprint = {
                        "platform": cb_config.get("platform", ""),
                        "country": cb_config.get("country", ""),
                        "screen_width": cb_config.get("screen_width", 0) or cb_config.get("screenWidth", 0),
                        "screen_height": cb_config.get("screen_height", 0) or cb_config.get("screenHeight", 0),
                        "timezone": cb_config.get("timezone", ""),
                        "gpu_renderer": cb_config.get("gpu_renderer", "") or cb_config.get("gpuRenderer", ""),
                        "google_email": cb_config.get("google_email", "") or cb_config.get("googleEmail", ""),
                    }

            # 2. Fallback: if config.json has no proxy, use database proxy
            if not proxy_url:
                proxy_url = k["proxy_url"]
                proxy_type = k["proxy_type"]

            results.append({
                "id": k["id"],
                "name": k["name"],
                "brand_name": k["brand_name"] or "",
                "industry": k["industry"] or "",
                "cloakbrowser_profile_name": profile_name,
                "proxy_url": proxy_url,
                "proxy_type": proxy_type,
                "fingerprint": fingerprint,
            })

        return jsonify({"code": 200, "brand_kits": results})
