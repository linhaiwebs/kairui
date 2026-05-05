import functools
import json
import logging

from flask import jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, verify_jwt_in_request

from config import config
from models import (
    create_site,
    delete_site,
    get_global_config,
    get_site,
    init_db,
    list_sites,
    update_global_config,
    update_site,
)
from panel_client import panel_client

logger = logging.getLogger(__name__)


def register_routes(app):

    # ---- Auth ----

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True) or {}
        username = data.get("username", "")
        password = data.get("password", "")

        if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
            token = create_access_token(identity=username)
            return jsonify({"code": 200, "message": "Login successful", "data": {"token": token, "username": username}})
        return jsonify({"code": 401, "message": "Invalid credentials", "data": None}), 401

    @app.route("/api/auth/check", methods=["GET"])
    @jwt_required()
    def auth_check():
        return jsonify({"code": 200, "message": "OK", "data": {"username": get_jwt_identity()}})

    # ---- Sites ----

    @app.route("/api/sites", methods=["GET"])
    @jwt_required()
    def get_sites():
        sites = list_sites()
        # Enrich with 1Panel live data
        try:
            panel_resp = panel_client.search_websites()
            if panel_resp.get("code") == 200:
                panel_sites = {s["id"]: s for s in panel_resp["data"].get("items", [])}
                for site in sites:
                    pwid = site.get("panel_website_id")
                    if pwid and pwid in panel_sites:
                        ps = panel_sites[pwid]
                        site["panel_status"] = ps.get("status", "")
                        site["panel_protocol"] = ps.get("protocol", "")
        except Exception as e:
            logger.warning(f"Failed to enrich sites from panel: {e}")
        return jsonify({"code": 200, "data": sites})

    @app.route("/api/sites", methods=["POST"])
    @jwt_required()
    def add_site():
        data = request.get_json(silent=True) or {}
        site = create_site(data)
        return jsonify({"code": 200, "data": site}), 201

    @app.route("/api/sites/<site_id>", methods=["GET"])
    @jwt_required()
    def get_site_detail(site_id):
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "Site not found"}), 404
        return jsonify({"code": 200, "data": site})

    @app.route("/api/sites/<site_id>", methods=["PUT"])
    @jwt_required()
    def edit_site(site_id):
        data = request.get_json(silent=True) or {}
        site = update_site(site_id, data)
        if not site:
            return jsonify({"code": 404, "message": "Site not found"}), 404
        return jsonify({"code": 200, "data": site})

    @app.route("/api/sites/<site_id>", methods=["DELETE"])
    @jwt_required()
    def remove_site(site_id):
        delete_site(site_id)
        return jsonify({"code": 200, "message": "Deleted"})

    # ---- CSV Export ----

    @app.route("/api/sites/export/csv", methods=["GET"])
    @jwt_required()
    def export_csv():
        import csv
        import io

        sites = list_sites()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Site Name", "Url", "Admin Name", "Admin Password", "Tag",
            "Security ID", "HTTP Username", "HTTP Password",
            "Verify Certificate", "SSL Version",
        ])
        for s in sites:
            writer.writerow([
                s.get("site_name", ""),
                s.get("url", ""),
                s.get("admin_name", ""),
                s.get("admin_password", ""),
                s.get("tag", ""),
                s.get("security_id", ""),
                s.get("http_username", ""),
                s.get("http_password", ""),
                "1" if s.get("verify_certificate") else "0",
                s.get("ssl_version", "auto"),
            ])
        output.seek(0)
        return app.response_class(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=wordpress_sites.csv"},
        )

    # ---- 1Panel Proxy APIs ----

    @app.route("/api/panel/apps/search", methods=["POST"])
    @jwt_required()
    def panel_search_apps():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.search_apps(
            name=data.get("name", ""),
            page=data.get("page", 1),
            page_size=data.get("pageSize", 100),
        ))

    @app.route("/api/panel/apps/<key>", methods=["GET"])
    @jwt_required()
    def panel_get_app(key):
        return jsonify(panel_client.get_app(key))

    @app.route("/api/panel/apps/detail/<int:app_id>/<version>", methods=["GET"])
    @jwt_required()
    def panel_get_app_detail(app_id, version):
        return jsonify(panel_client.get_app_detail(app_id, version))

    @app.route("/api/panel/apps/install", methods=["POST"])
    @jwt_required()
    def panel_install_app():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.install_app(
            app_detail_id=data.get("appDetailId"),
            name=data.get("name"),
            params=data.get("params", {}),
            services=data.get("services"),
            advanced=data.get("advanced", False),
        ))

    @app.route("/api/panel/apps/installed/search", methods=["POST"])
    @jwt_required()
    def panel_search_installed():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.search_installed_apps(
            page=data.get("page", 1),
            page_size=data.get("pageSize", 100),
            name=data.get("name", ""),
            app_type=data.get("type", ""),
        ))

    @app.route("/api/panel/apps/installed/list", methods=["GET"])
    @jwt_required()
    def panel_installed_list():
        return jsonify(panel_client.get_installed_list())

    @app.route("/api/panel/apps/installed/params/<int:install_id>", methods=["GET"])
    @jwt_required()
    def panel_installed_params(install_id):
        return jsonify(panel_client.get_installed_params(install_id))

    @app.route("/api/panel/apps/services/<key>", methods=["GET"])
    @jwt_required()
    def panel_app_services(key):
        return jsonify(panel_client.get_app_services(key))

    @app.route("/api/panel/websites/search", methods=["POST"])
    @jwt_required()
    def panel_search_websites():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.search_websites(
            page=data.get("page", 1),
            page_size=data.get("pageSize", 100),
            name=data.get("name", ""),
        ))

    @app.route("/api/panel/websites/list", methods=["GET"])
    @jwt_required()
    def panel_websites_list():
        return jsonify(panel_client.get_websites_list())

    @app.route("/api/panel/websites/create", methods=["POST"])
    @jwt_required()
    def panel_create_website():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.create_website(
            primary_domain=data.get("primaryDomain", ""),
            alias=data.get("alias", ""),
            app_type=data.get("appType", "installed"),
            app_install_id=data.get("appInstallID"),
            app_detail_id=data.get("appDetailID"),
            app_id=data.get("appID"),
            website_group_id=data.get("webSiteGroupID", 1),
            remark=data.get("remark", ""),
            other_domains=data.get("otherDomains", ""),
            proxy=data.get("proxy", ""),
        ))

    @app.route("/api/panel/websites/check", methods=["POST"])
    @jwt_required()
    def panel_check_website():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.check_website(
            primary_domain=data.get("primaryDomain", ""),
            app_type=data.get("appType", "installed"),
            app_install_id=data.get("appInstallID"),
            alias=data.get("alias", ""),
        ))

    @app.route("/api/panel/websites/<int:website_id>", methods=["GET"])
    @jwt_required()
    def panel_get_website(website_id):
        return jsonify(panel_client.get_website(website_id))

    @app.route("/api/panel/websites/<int:website_id>", methods=["DELETE"])
    @jwt_required()
    def panel_delete_website(website_id):
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.delete_website(
            website_id=website_id,
            delete_app=data.get("deleteApp", True),
            delete_backup=data.get("deleteBackup", True),
            force_delete=data.get("forceDelete", False),
        ))

    @app.route("/api/panel/groups/search", methods=["POST"])
    @jwt_required()
    def panel_search_groups():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.search_groups(data.get("type", "website")))

    @app.route("/api/panel/websites/operate", methods=["POST"])
    @jwt_required()
    def panel_operate_website():
        data = request.get_json(silent=True) or {}
        return jsonify(panel_client.operate_website(
            website_id=data.get("id"),
            operate=data.get("operate", ""),
        ))

    # ---- WordPress Batch Creation ----

    @app.route("/api/wordpress/batch-create", methods=["POST"])
    @jwt_required()
    def batch_create_wordpress():
        """Create multiple WordPress sites in batch via 1Panel."""
        data = request.get_json(silent=True) or {}
        domains = data.get("domains", [])
        if not domains:
            return jsonify({"code": 400, "message": "No domains provided"}), 400

        global_cfg = get_global_config()
        default_admin = data.get("admin_name") or json.loads(global_cfg.get("default_admin_name", '"admin"'))
        default_password = data.get("admin_password") or global_cfg.get("default_admin_password", "")
        tag = data.get("tag", "")
        security_id = data.get("security_id", "")
        http_username = data.get("http_username", "")
        http_password = data.get("http_password", "")
        verify_cert = data.get("verify_certificate", True)
        ssl_version = data.get("ssl_version", "auto")

        # Get WordPress app info
        app_resp = panel_client.get_app("wordpress")
        if app_resp.get("code") != 200:
            return jsonify({"code": 500, "message": "Failed to get WordPress app info from 1Panel"}), 500

        app_data = app_resp["data"]
        app_id = app_data["id"]
        versions = app_data.get("versions", [])
        version = versions[0] if versions else "6.9.4"

        # Get app detail for the latest version
        detail_resp = panel_client.get_app_detail(app_id, version)
        if detail_resp.get("code") != 200:
            return jsonify({"code": 500, "message": "Failed to get WordPress app detail"}), 500

        app_detail_id = detail_resp["data"]["id"]

        # Get database service info
        db_service = data.get("db_service") or global_cfg.get("db_service", "mariadb")
        db_services_resp = panel_client.get_app_services(db_service)
        if db_services_resp.get("code") != 200:
            return jsonify({"code": 500, "message": "Failed to get database service info"}), 500

        db_service_info = db_services_resp["data"][0] if db_services_resp["data"] else None
        if not db_service_info:
            return jsonify({"code": 500, "message": "No database service available"}), 500

        db_config = db_service_info.get("config", {})
        db_host = db_service_info.get("label", "mariadb")
        db_port = 3306

        results = []
        base_port = data.get("base_port", 8081)
        used_ports = set()

        # Get currently used ports
        try:
            installed_resp = panel_client.search_installed_apps(name="wordpress")
            if installed_resp.get("code") == 200:
                for item in installed_resp["data"].get("items", []):
                    if item.get("httpPort"):
                        used_ports.add(item["httpPort"])
        except Exception:
            pass

        for i, domain in enumerate(domains):
            domain = domain.strip()
            if not domain:
                continue

            alias = domain.replace(".", "-")
            site_name = domain

            # Find available port
            port = base_port + i
            while port in used_ports:
                port += 1
            used_ports.add(port)

            # Generate DB credentials
            import string
            import random
            db_name = f"wp_{alias[:20]}"
            db_user = f"wp_{alias[:20]}"
            db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=12))

            # Install WordPress app
            install_params = {
                "PANEL_DB_TYPE": db_service,
                "PANEL_DB_NAME": db_name,
                "PANEL_DB_USER": db_user,
                "PANEL_DB_USER_PASSWORD": db_pass,
                "PANEL_APP_PORT_HTTP": port,
            }

            services = {db_service: db_service_info.get("value", db_service)}

            install_resp = panel_client.install_app(
                app_detail_id=app_detail_id,
                name=alias,
                params=install_params,
                services=services,
            )

            if install_resp.get("code") != 200:
                results.append({"domain": domain, "status": "error", "message": install_resp.get("message", "Install failed")})
                continue

            # Get the installed app ID
            app_install_id = install_resp.get("data")
            if isinstance(app_install_id, dict):
                app_install_id = app_install_id.get("id")

            # Create website
            website_resp = panel_client.create_website(
                primary_domain=domain,
                alias=alias,
                app_type="installed",
                app_install_id=app_install_id,
                website_group_id=data.get("website_group_id", 1),
                proxy=f"http://127.0.0.1:{port}",
            )

            panel_website_id = None
            if website_resp.get("code") == 200:
                panel_website_id = website_resp.get("data")
                if isinstance(panel_website_id, dict):
                    panel_website_id = panel_website_id.get("id")

            # Save to local DB
            site = create_site({
                "site_name": site_name,
                "url": f"http://{domain}",
                "admin_name": default_admin,
                "admin_password": default_password,
                "tag": tag,
                "security_id": security_id,
                "http_username": http_username,
                "http_password": http_password,
                "verify_certificate": verify_cert,
                "ssl_version": ssl_version,
                "panel_website_id": panel_website_id,
                "panel_app_install_id": app_install_id,
                "panel_app_detail_id": app_detail_id,
            })

            results.append({
                "domain": domain,
                "status": "success",
                "port": port,
                "site_id": site["id"] if site else None,
                "panel_website_id": panel_website_id,
                "panel_app_install_id": app_install_id,
            })

        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = sum(1 for r in results if r["status"] == "error")

        return jsonify({
            "code": 200,
            "data": {
                "results": results,
                "total": len(results),
                "success": success_count,
                "error": error_count,
            },
        })

    # ---- Global Config ----

    @app.route("/api/config", methods=["GET"])
    @jwt_required()
    def get_config():
        cfg = get_global_config()
        # Parse JSON values
        for key in ["default_plugins", "default_themes"]:
            if key in cfg:
                try:
                    cfg[key] = json.loads(cfg[key])
                except (json.JSONDecodeError, TypeError):
                    cfg[key] = []
        return jsonify({"code": 200, "data": cfg})

    @app.route("/api/config", methods=["PUT"])
    @jwt_required()
    def save_config():
        data = request.get_json(silent=True) or {}
        for key, value in data.items():
            update_global_config(key, value)
        return jsonify({"code": 200, "message": "Config saved"})

    # ---- 1Panel Status ----

    @app.route("/api/panel/status", methods=["GET"])
    @jwt_required()
    def panel_status():
        try:
            resp = panel_client.search_apps(name="wordpress")
            if resp.get("code") == 200:
                return jsonify({"code": 200, "data": {"connected": True}})
            return jsonify({"code": 200, "data": {"connected": False, "message": resp.get("message", "")}})
        except Exception as e:
            return jsonify({"code": 200, "data": {"connected": False, "message": str(e)}})
