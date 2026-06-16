# Kairui 凯瑞指纹 Bridge API
# Operator login → token → read brand kits with proxy + fingerprint

import json
import os as _os
import secrets
from flask import request, jsonify
from werkzeug.security import check_password_hash

# In-memory token store — tokens expire on server restart
_tokens: dict[str, dict] = {}


def _require_token():
    """Validate Bearer token. Returns (ok, operator_info_or_error)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False, jsonify({"code": 401, "message": "需要登录"}), 401
    token = auth[7:]
    info = _tokens.get(token)
    if not info:
        return False, jsonify({"code": 401, "message": "登录已过期，请重新登录"}), 401
    return True, info


def _read_cloakbrowser_config(profile_name):
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
        return jsonify({"code": 200, "server": "kairui", "version": "1.0"})

    @app.route("/api/bridge/login", methods=["POST"])
    def bridge_login():
        """Login with operator username + password. Returns token + operator info."""
        data = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "")
        if not username or not password:
            return jsonify({"code": 400, "message": "用户名和密码不能为空"}), 400

        from models import get_db
        db = get_db()
        user = db.execute(
            "SELECT u.id, u.username, u.password, u.role, "
            "pe.name as panel_name, pe.host as panel_host "
            "FROM users u "
            "LEFT JOIN panel_environments pe ON u.panel_environment_id = pe.id "
            "WHERE u.username = ? AND u.role = 'operator'",
            (username,)
        ).fetchone()

        if not user or not check_password_hash(user["password"], password):
            return jsonify({"code": 401, "message": "用户名或密码错误"}), 401

        token = secrets.token_hex(32)
        info = {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "panel_name": user["panel_name"],
            "panel_host": user["panel_host"],
        }
        _tokens[token] = info
        return jsonify({"code": 200, "token": token, "operator": info})

    @app.route("/api/bridge/brand-kits", methods=["GET"])
    def bridge_brand_kits():
        """List brand kits for the logged-in operator."""
        ok, info = _require_token()
        if not ok:
            return info
        operator_id = info["id"]

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
