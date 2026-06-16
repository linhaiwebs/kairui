# Kairui 凯瑞指纹 Bridge API
# Imported by routes.py — provides endpoints for the 凯瑞指纹 desktop app
# to list operators and import brand kits as browser profiles.
# Auth: X-API-Key header (from KAIURI_BRIDGE_API_KEY env var, default "kairui-bridge-default-key")

import os as _os
from flask import request, jsonify


def _bridge_api_key():
    return _os.environ.get("KAIURI_BRIDGE_API_KEY", "kairui-bridge-default-key")


def _bridge_auth():
    api_key = request.headers.get("X-API-Key", "")
    if api_key != _bridge_api_key():
        return False, jsonify({"code": 401, "message": "unauthorized: invalid API key"}), 401
    return True, None


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
        # Import here to avoid circular dependency
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
            "p.proxy_url, p.proxy_type, p.ip as proxy_ip "
            "FROM brand_kits bk "
            "LEFT JOIN proxies p ON bk.proxy_id = p.id "
            "WHERE bk.created_by = ? ORDER BY bk.id",
            (operator_id,)
        ).fetchall()
        return jsonify({
            "code": 200,
            "brand_kits": [
                {
                    "id": k["id"],
                    "name": k["name"],
                    "brand_name": k["brand_name"] or "",
                    "industry": k["industry"] or "",
                    "cloakbrowser_profile_name": k["cloakbrowser_profile_name"],
                    "proxy_url": k["proxy_url"],
                    "proxy_type": k["proxy_type"],
                    "proxy_ip": k["proxy_ip"],
                }
                for k in kits
            ]
        })
