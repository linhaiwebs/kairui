import functools
import json
import logging
import os
import subprocess
import threading
import time
import uuid

import requests as http_requests

from flask import jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, verify_jwt_in_request

from config import config
from models import (
    create_bg_task,
    create_plugin,
    create_site,
    delete_plugin,
    delete_site,
    get_bg_task,
    get_enabled_plugins,
    get_global_config,
    get_plugin,
    get_site,
    init_db,
    list_plugins,
    list_sites,
    update_bg_task,
    update_global_config,
    update_site,
)
from panel_client import panel_client

logger = logging.getLogger(__name__)

# Directory to store uploaded plugin files
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)


def install_plugins_to_site(site_url, admin_user, admin_password, plugin_ids):
    """Install plugins to a WordPress site via wp-admin HTTP API.
    
    Steps:
    1. Login to wp-admin via HTTP
    2. Get upload nonce from plugin-install page
    3. Upload each plugin zip via wp-admin/update.php
    4. Activate each plugin via wp-admin/plugins.php
    """
    import re as _re
    
    results = []
    
    try:
        # Login to WordPress
        session = http_requests.Session()
        login_resp = session.post(
            f"{site_url}/wp-login.php",
            data={
                "log": admin_user,
                "pwd": admin_password,
                "wp-submit": "Log In",
                "redirect_to": f"{site_url}/wp-admin/",
            },
            timeout=30,
            allow_redirects=True,
        )
        if login_resp.status_code != 200 or "wp-admin" not in login_resp.url:
            return [{"plugin": p, "status": "error", "message": "WordPress登录失败"} for p in plugin_ids]
        
        for pid in plugin_ids:
            plugin = get_plugin(pid)
            if not plugin or not plugin.get("file_path") or not os.path.isfile(plugin["file_path"]):
                results.append({"plugin": pid, "status": "error", "message": "插件文件不存在"})
                continue

            plugin_name = plugin["name"]
            plugin_filename = plugin["filename"]

            try:
                # Get upload nonce from plugin-install page
                upload_page = session.get(
                    f"{site_url}/wp-admin/plugin-install.php?tab=upload",
                    timeout=15,
                )
                nonce_match = _re.search(r'_wpnonce["\']\s*value=["\']([^"\']+)', upload_page.text)
                if not nonce_match:
                    results.append({
                        "plugin": plugin_name, "status": "error",
                        "message": "无法获取上传nonce"
                    })
                    continue
                nonce = nonce_match.group(1)

                # Upload plugin zip
                with open(plugin["file_path"], "rb") as f:
                    upload_resp = session.post(
                        f"{site_url}/wp-admin/update.php?action=upload-plugin",
                        files={"pluginzip": (plugin_filename, f, "application/zip")},
                        data={"_wpnonce": nonce},
                        timeout=60,
                    )

                if upload_resp.status_code != 200:
                    results.append({
                        "plugin": plugin_name, "status": "error",
                        "message": f"上传失败: HTTP {upload_resp.status_code}"
                    })
                    continue

                # Check if plugin was installed and find activation link
                act_match = _re.search(
                    r'action=activate[^"]*plugin=([^"&]+)[^"]*_wpnonce=([a-f0-9]+)',
                    upload_resp.text,
                )
                if act_match:
                    plugin_path = act_match.group(1)
                    act_nonce = act_match.group(2)
                    
                    # Activate the plugin
                    act_resp = session.get(
                        f"{site_url}/wp-admin/plugins.php?action=activate&plugin={plugin_path}&_wpnonce={act_nonce}",
                        timeout=30,
                        allow_redirects=True,
                    )
                    if act_resp.status_code == 200:
                        results.append({
                            "plugin": plugin_name, "status": "success",
                            "message": "插件已安装并启用"
                        })
                    else:
                        results.append({
                            "plugin": plugin_name, "status": "success",
                            "message": "插件已安装，启用需确认"
                        })
                elif "已安装" in upload_resp.text or "already" in upload_resp.text.lower():
                    results.append({
                        "plugin": plugin_name, "status": "success",
                        "message": "插件已存在"
                    })
                else:
                    results.append({
                        "plugin": plugin_name, "status": "error",
                        "message": "上传后未找到激活链接"
                    })

            except http_requests.Timeout:
                results.append({"plugin": plugin_name, "status": "error", "message": "请求超时"})
            except Exception as e:
                results.append({"plugin": plugin_name, "status": "error", "message": str(e)[:100]})

    except Exception as e:
        logger.error(f"Plugin install session error: {e}")
        results = [{"plugin": p, "status": "error", "message": f"会话错误: {str(e)[:80]}"} for p in plugin_ids]

    return results


def auto_install_wordpress(container_name, site_url, site_title, admin_user, admin_password, admin_email, port=None):
    """Auto-complete WordPress installation via HTTP POST to wp-admin/install.php.
    
    This bypasses the WordPress install page so the site is immediately usable.
    Uses direct HTTP requests to the WordPress install endpoint.
    """
    import re
    
    # Determine the WordPress URL
    from config import config as app_config
    server_ip = app_config.PANEL_SERVER_IP
    wp_base_url = f"http://{server_ip}:{port}" if port else site_url
    
    logger.info(f"Starting WP auto-install for {site_url} (base: {wp_base_url})")

    # Wait for WordPress to be ready (up to 120 seconds)
    wp_ready = False
    for attempt in range(24):
        try:
            resp = http_requests.get(f"{wp_base_url}/", timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                wp_ready = True
                logger.info(f"WP ready after {(attempt+1)*5}s for {site_url}")
                break
            else:
                logger.info(f"WP not ready (attempt {attempt+1}): status={resp.status_code}")
        except Exception as e:
            logger.info(f"WP not ready (attempt {attempt+1}): {str(e)[:80]}")
        time.sleep(5)

    if not wp_ready:
        return {"success": False, "message": "WordPress容器启动超时，无法访问"}

    # Small additional wait for database connection to stabilize
    time.sleep(3)

    # Check if WordPress is already installed
    try:
        resp = http_requests.get(f"{wp_base_url}/wp-admin/install.php", timeout=10, allow_redirects=True)
        if "already installed" in resp.text.lower() or "已安装" in resp.text:
            logger.info(f"WordPress already installed for {site_url}")
            return {"success": True, "message": "WordPress已安装"}
    except Exception:
        pass

    # First, try to set language to zh_CN
    try:
        lang_resp = http_requests.post(
            f"{wp_base_url}/wp-admin/install.php?step=1",
            data={"language": "zh_CN"},
            timeout=30,
        )
    except Exception:
        pass  # Language setup is optional

    # Submit the WordPress installation form
    try:
        install_resp = http_requests.post(
            f"{wp_base_url}/wp-admin/install.php?step=2",
            data={
                "weblog_title": site_title or "WordPress",
                "user_name": admin_user,
                "admin_password": admin_password,
                "admin_password2": admin_password,
                "pw_weak": "1",
                "admin_email": admin_email,
                "blog_public": "1",
                "language": "zh_CN",
            },
            timeout=60,
            allow_redirects=True,
        )

        if install_resp.status_code == 200:
            # Check for success indicators
            if "success" in install_resp.text.lower() or "成功" in install_resp.text or "installed" in install_resp.text.lower():
                logger.info(f"WordPress auto-installed successfully for {site_url}")
                return {"success": True, "message": "WordPress安装成功"}

            # Check for "already installed" (race condition)
            if "already installed" in install_resp.text.lower():
                return {"success": True, "message": "WordPress已安装"}

            # Check for errors
            errors = re.findall(r'<p[^>]*class="[^"]*error[^"]*"[^>]*>(.*?)</p>', install_resp.text, re.DOTALL)
            error_msg = re.sub(r'<[^>]+>', '', errors[0]).strip() if errors else ""
            if error_msg:
                logger.warning(f"WP install error for {site_url}: {error_msg[:200]}")
                return {"success": False, "message": f"安装错误: {error_msg[:200]}"}

            # If we can't determine, verify by trying to access wp-login
            try:
                login_check = http_requests.get(f"{wp_base_url}/wp-login.php", timeout=10)
                if login_check.status_code == 200:
                    return {"success": True, "message": "WordPress安装成功（验证通过）"}
            except Exception:
                pass

            logger.warning(f"WP install status uncertain for {site_url}")
            return {"success": False, "message": "安装状态不确定，请手动检查"}

        return {"success": False, "message": f"安装请求返回 {install_resp.status_code}"}

    except http_requests.exceptions.Timeout:
        return {"success": False, "message": "WP install request timeout"}
    except Exception as e:
        logger.error(f"WP install exception for {site_url}: {e}")
        return {"success": False, "message": f"Install error: {str(e)[:100]}"}


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
        try:
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
        except Exception as e:
            logger.error(f"Failed to list sites: {e}")
            return jsonify({"code": 500, "message": f"获取站点列表失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites", methods=["POST"])
    @jwt_required()
    def add_site():
        try:
            data = request.get_json(silent=True) or {}
            site = create_site(data)
            return jsonify({"code": 200, "data": site}), 201
        except Exception as e:
            logger.error(f"Failed to create site: {e}")
            return jsonify({"code": 500, "message": f"创建站点失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<site_id>", methods=["GET"])
    @jwt_required()
    def get_site_detail(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "Site not found"}), 404
            return jsonify({"code": 200, "data": site})
        except Exception as e:
            logger.error(f"Failed to get site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"获取站点详情失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<site_id>", methods=["PUT"])
    @jwt_required()
    def edit_site(site_id):
        try:
            data = request.get_json(silent=True) or {}
            site = update_site(site_id, data)
            if not site:
                return jsonify({"code": 404, "message": "Site not found"}), 404
            return jsonify({"code": 200, "data": site})
        except Exception as e:
            logger.error(f"Failed to update site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"更新站点失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<site_id>", methods=["DELETE"])
    @jwt_required()
    def remove_site(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            # Clean up 1Panel resources
            if site.get("panel_website_id"):
                # Deployment website: deleting via 1Panel will clean up nginx config + app
                try:
                    panel_client.delete_website(
                        site["panel_website_id"],
                        delete_app=True, delete_backup=True, force_delete=True,
                    )
                    logger.info(f"Deleted 1Panel deployment website {site['panel_website_id']}")
                except Exception as e:
                    logger.warning(f"Failed to delete 1Panel website: {e}")
            else:
                # Legacy: manually clean up nginx config and app
                nginx_alias = site.get("nginx_alias")
                if nginx_alias:
                    try:
                        panel_client.delete_nginx_proxy_config(alias=nginx_alias)
                        logger.info(f"Deleted nginx config for {nginx_alias}")
                    except Exception as e:
                        logger.warning(f"Failed to delete nginx config: {e}")

                if site.get("panel_app_install_id"):
                    try:
                        panel_client.operate_installed(
                            site["panel_app_install_id"], "delete",
                            force_delete=True, delete_backup=True, delete_db=True,
                        )
                        logger.info(f"Deleted 1Panel app install {site['panel_app_install_id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete 1Panel app: {e}")

            delete_site(site_id)
            return jsonify({"code": 200, "message": "站点已删除"})
        except Exception as e:
            logger.error(f"Failed to delete site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"删除站点失败: {str(e)[:100]}"}), 500

    # ---- CSV Export ----

    @app.route("/api/sites/export/csv", methods=["GET"])
    @jwt_required()
    def export_csv():
        try:
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
        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            return jsonify({"code": 500, "message": f"导出CSV失败: {str(e)[:100]}"}), 500

    # ---- 1Panel Proxy APIs ----

    @app.route("/api/panel/apps/search", methods=["POST"])
    @jwt_required()
    def panel_search_apps():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.search_apps(
                name=data.get("name", ""),
                page=data.get("page", 1),
                page_size=data.get("pageSize", 100),
            ))
        except Exception as e:
            logger.error(f"Panel search apps failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/<key>", methods=["GET"])
    @jwt_required()
    def panel_get_app(key):
        try:
            return jsonify(panel_client.get_app(key))
        except Exception as e:
            logger.error(f"Panel get app failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/detail/<int:app_id>/<version>", methods=["GET"])
    @jwt_required()
    def panel_get_app_detail(app_id, version):
        try:
            return jsonify(panel_client.get_app_detail(app_id, version))
        except Exception as e:
            logger.error(f"Panel get app detail failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/install", methods=["POST"])
    @jwt_required()
    def panel_install_app():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.install_app(
                app_detail_id=data.get("appDetailId"),
                name=data.get("name"),
                params=data.get("params", {}),
                services=data.get("services"),
                advanced=data.get("advanced", False),
            ))
        except Exception as e:
            logger.error(f"Panel install app failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/installed/search", methods=["POST"])
    @jwt_required()
    def panel_search_installed():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.search_installed_apps(
                page=data.get("page", 1),
                page_size=data.get("pageSize", 100),
                name=data.get("name", ""),
                app_type=data.get("type", ""),
            ))
        except Exception as e:
            logger.error(f"Panel search installed failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/installed/list", methods=["GET"])
    @jwt_required()
    def panel_installed_list():
        try:
            return jsonify(panel_client.get_installed_list())
        except Exception as e:
            logger.error(f"Panel installed list failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/installed/params/<int:install_id>", methods=["GET"])
    @jwt_required()
    def panel_installed_params(install_id):
        try:
            return jsonify(panel_client.get_installed_params(install_id))
        except Exception as e:
            logger.error(f"Panel installed params failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/services/<key>", methods=["GET"])
    @jwt_required()
    def panel_app_services(key):
        try:
            return jsonify(panel_client.get_app_services(key))
        except Exception as e:
            logger.error(f"Panel app services failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/search", methods=["POST"])
    @jwt_required()
    def panel_search_websites():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.search_websites(
                page=data.get("page", 1),
                page_size=data.get("pageSize", 100),
                name=data.get("name", ""),
            ))
        except Exception as e:
            logger.error(f"Panel search websites failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/list", methods=["GET"])
    @jwt_required()
    def panel_websites_list():
        try:
            return jsonify(panel_client.get_websites_list())
        except Exception as e:
            logger.error(f"Panel websites list failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/create", methods=["POST"])
    @jwt_required()
    def panel_create_website():
        try:
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
        except Exception as e:
            logger.error(f"Panel create website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/check", methods=["POST"])
    @jwt_required()
    def panel_check_website():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.check_website(
                primary_domain=data.get("primaryDomain", ""),
                app_type=data.get("appType", "installed"),
                app_install_id=data.get("appInstallID"),
                alias=data.get("alias", ""),
            ))
        except Exception as e:
            logger.error(f"Panel check website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/<int:website_id>", methods=["GET"])
    @jwt_required()
    def panel_get_website(website_id):
        try:
            return jsonify(panel_client.get_website(website_id))
        except Exception as e:
            logger.error(f"Panel get website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/<int:website_id>", methods=["DELETE"])
    @jwt_required()
    def panel_delete_website(website_id):
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.delete_website(
                website_id=website_id,
                delete_app=data.get("deleteApp", True),
                delete_backup=data.get("deleteBackup", True),
                force_delete=data.get("forceDelete", False),
            ))
        except Exception as e:
            logger.error(f"Panel delete website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/groups/search", methods=["POST"])
    @jwt_required()
    def panel_search_groups():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.search_groups(data.get("type", "website")))
        except Exception as e:
            logger.error(f"Panel search groups failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/operate", methods=["POST"])
    @jwt_required()
    def panel_operate_website():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(panel_client.operate_website(
                website_id=data.get("id"),
                operate=data.get("operate", ""),
            ))
        except Exception as e:
            logger.error(f"Panel operate website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    # ---- WordPress Batch Creation ----

    @app.route("/api/wordpress/batch-create", methods=["POST"])
    @jwt_required()
    def batch_create_wordpress():
        """Create multiple WordPress sites in batch via 1Panel.
        
        Workflow:
        1. For each domain, create a database via 1Panel's /databases API
        2. Install WordPress with PANEL_DB_HOST='mariadb' (1Panel DB name)
        3. 1Panel auto-resolves DB host address and sets PANEL_DB_PORT
        4. If allowPort wasn't applied during install, fix via update_installed API
        """
        try:
            data = request.get_json(silent=True) or {}
            domains = data.get("domains", [])
            if not domains:
                return jsonify({"code": 400, "message": "未提供域名"}), 400

            global_cfg = get_global_config()
            try:
                default_admin = data.get("admin_name") or json.loads(global_cfg.get("default_admin_name", '"admin"'))
            except (json.JSONDecodeError, TypeError):
                default_admin = data.get("admin_name", "admin")
            default_password = data.get("admin_password") or global_cfg.get("default_admin_password", "")
            tag = data.get("tag", "")
            security_id = data.get("security_id", "")
            http_username = data.get("http_username", "")
            http_password = data.get("http_password", "")
            verify_cert = data.get("verify_certificate", True)
            ssl_version = data.get("ssl_version", "auto")
            plugin_ids = data.get("plugin_ids", [])

            # Get WordPress app info
            app_resp = panel_client.get_app("wordpress")
            if app_resp.get("code") != 200:
                return jsonify({"code": 500, "message": f"从1Panel获取WordPress应用信息失败: {app_resp.get('message', '')}"}), 500

            app_data = app_resp.get("data", {})
            if not app_data:
                return jsonify({"code": 500, "message": "WordPress应用数据为空"}), 500
            app_id = app_data.get("id")
            if not app_id:
                return jsonify({"code": 500, "message": "无法获取WordPress应用ID"}), 500
            versions = app_data.get("versions", [])
            version = versions[0] if versions else "6.9.4"

            # Get app detail for the latest version
            detail_resp = panel_client.get_app_detail(app_id, version)
            if detail_resp.get("code") != 200:
                return jsonify({"code": 500, "message": f"获取WordPress应用详情失败: {detail_resp.get('message', '')}"}), 500

            detail_data = detail_resp.get("data", {})
            if not detail_data:
                return jsonify({"code": 500, "message": "WordPress应用详情数据为空"}), 500
            app_detail_id = detail_data.get("id")

            # Get database service name (default: mariadb)
            db_service = data.get("db_service") or global_cfg.get("db_service", "mariadb")

            # Verify the database service exists in 1Panel
            db_installed_resp = panel_client.search_installed_apps(name=db_service)
            if db_installed_resp.get("code") != 200 or not db_installed_resp.get("data", {}).get("items", []):
                return jsonify({"code": 500, "message": f"数据库服务 {db_service} 未安装或未运行，请先在1Panel中安装MariaDB"}), 500

            results = []
            base_port = data.get("base_port", 8081)
            used_ports = set()

            # Get ALL currently used ports from 1Panel
            try:
                installed_resp = panel_client.search_installed_apps(page=1, page_size=200)
                if installed_resp.get("code") == 200:
                    for item in installed_resp.get("data", {}).get("items", []):
                        if item.get("httpPort"):
                            used_ports.add(item["httpPort"])
                        if item.get("httpsPort"):
                            used_ports.add(item["httpsPort"])
            except Exception:
                pass

            # Also scan host ports for safety
            try:
                port_scan = subprocess.run(
                    ["ss", "-tlnp", "-H"], capture_output=True, text=True, timeout=5
                )
                if port_scan.returncode == 0:
                    for line in port_scan.stdout.strip().split("\n"):
                        parts = line.split()
                        for p in parts:
                            if ":" in p:
                                port_str = p.rsplit(":", 1)[-1]
                                try:
                                    used_ports.add(int(port_str))
                                except ValueError:
                                    pass
            except Exception:
                pass

            def find_available_port(start):
                port = start
                while port in used_ports:
                    port += 1
                used_ports.add(port)
                return port

            for i, domain in enumerate(domains):
                domain = domain.strip()
                if not domain:
                    continue

                alias = domain.replace(".", "-")
                site_name = domain

                # Find available port
                port = find_available_port(base_port)

                # Generate DB credentials
                import string
                import random
                db_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
                db_name = f"wp_{db_suffix}"
                db_user = f"wp_{db_suffix}"
                db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=16))

                # Step 1: Create database via 1Panel's /databases API
                # This is critical - 1Panel resolves PANEL_DB_HOST from its database records
                db_created = False
                try:
                    db_api_resp = panel_client.create_database(
                        name=db_name,
                        db_type=db_service,
                        username=db_user,
                        password=db_pass,
                        permission="%",
                        format_str="utf8mb4",
                    )
                    if db_api_resp.get("code") == 200:
                        db_created = True
                        logger.info(f"Database {db_name} created via 1Panel API")
                    else:
                        logger.warning(f"1Panel DB API failed: {db_api_resp.get('message', '')}")
                except Exception as e:
                    logger.warning(f"1Panel DB API exception: {e}")

                if not db_created:
                    results.append({
                        "domain": domain, "status": "error",
                        "message": f"创建数据库 {db_name} 失败，请检查1Panel数据库服务"
                    })
                    continue

                # Step 2: Install WordPress with PANEL_DB_HOST = db_service name
                # 1Panel resolves this to the actual service address and sets PANEL_DB_PORT
                install_params = {
                    "PANEL_DB_TYPE": db_service,
                    "PANEL_DB_HOST": db_service,  # 1Panel DB name, NOT container name
                    "PANEL_DB_NAME": db_name,
                    "PANEL_DB_USER": db_user,
                    "PANEL_DB_USER_PASSWORD": db_pass,
                    "PANEL_APP_PORT_HTTP": port,
                }

                services = {db_service: db_service}

                try:
                    install_resp = panel_client.install_app(
                        app_detail_id=app_detail_id,
                        name=alias,
                        params=install_params,
                        services=services,
                        advanced=True,
                        allow_port=True,
                    )
                except Exception as e:
                    results.append({"domain": domain, "status": "error", "message": f"安装请求失败: {str(e)[:80]}"})
                    continue

                if install_resp.get("code") != 200:
                    results.append({"domain": domain, "status": "error", "message": install_resp.get("message", "安装失败")})
                    continue

                # Wait for install to register
                time.sleep(3)

                # Get the installed app ID
                app_install_id = None
                container_name = None
                try:
                    new_installed = panel_client.search_installed_apps(name=alias)
                    if new_installed.get("code") == 200:
                        new_app = next(
                            (a for a in new_installed.get("data", {}).get("items", []) if a.get("name") == alias),
                            None,
                        )
                        if new_app:
                            app_install_id = new_app.get("id")
                            container_name = new_app.get("container", "")
                except Exception:
                    pass

                # Step 3: If allowPort wasn't applied, fix via update API
                if app_install_id:
                    try:
                        update_resp = panel_client.update_installed(
                            install_id=app_install_id,
                            params={"PANEL_APP_PORT_HTTP": port},
                            advanced=True,
                            allow_port=True,
                        )
                        if update_resp.get("code") != 200:
                            logger.warning(f"Update allowPort failed for {alias}: {update_resp.get('message', '')}")
                    except Exception as e:
                        logger.warning(f"Update allowPort exception for {alias}: {e}")

                # Step 4: Create deployment website in 1Panel (一键部署)
                # 1Panel uses OpenResty to auto-configure reverse proxy for the installed WP app
                # - alias = domain-derived (matches installed app name)
                # - appType = "installed" links to the installed WP app
                # - webSiteGroupID = default group (ID=1)
                # - IPV6 = True
                website_result = None
                panel_website_id = None
                try:
                    # Ensure website group exists
                    group_id = panel_client.ensure_website_group()
                    
                    # The alias must match the installed app name
                    website_result = panel_client.create_website(
                        primary_domain=domain,
                        alias=alias,
                        app_type="installed",
                        app_install_id=app_install_id,
                        website_group_id=group_id,
                        enable_ipv6=True,
                    )
                    if website_result.get("code") == 200:
                        logger.info(f"Deployment website created for {domain} (app_install_id={app_install_id})")
                        # Find the website ID by searching
                        ws = panel_client.search_websites(name=domain)
                        if ws.get("code") == 200:
                            items = ws.get("data", {}).get("items") or []
                            for w in items:
                                if w.get("alias") == alias:
                                    panel_website_id = w.get("id")
                                    break
                    else:
                        logger.warning(f"Create deployment website failed for {domain}: {website_result.get('message', '')}")
                except Exception as e:
                    logger.warning(f"Create deployment website exception for {domain}: {e}")
                    website_result = {"code": 500, "message": str(e)[:80]}

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
                    "port": port,
                    "nginx_alias": alias,
                })

                # Step 5: Auto-complete WordPress installation (background thread)
                wp_install_result = None
                site_id_for_bg = site["id"] if site else str(uuid.uuid4())[:8]

                def _bg_wp_install(sid, cn, surl, stitle, auser, apwd, aemail, aport, pids=None):
                    """Background thread to auto-install WordPress and then install plugins."""
                    try:
                        update_bg_task(sid, status="installing", message="WordPress安装中...")
                        result = auto_install_wordpress(
                            container_name=cn,
                            site_url=surl,
                            site_title=stitle,
                            admin_user=auser,
                            admin_password=apwd,
                            admin_email=aemail,
                            port=aport,
                        )
                        final_status = "installed" if result.get("success") else "failed"
                        update_bg_task(sid, status=final_status, message=result.get("message", ""))
                        logger.info(f"BG WP install for {surl}: {result.get('message', '')}")
                        
                        # Install plugins after WP is ready
                        if result.get("success") and pids:
                            try:
                                update_bg_task(sid, status="installed", message=f"WordPress已安装，正在安装{len(pids)}个插件...")
                                from config import config as app_config
                                wp_url = f"http://{app_config.PANEL_SERVER_IP}:{aport}" if aport else surl
                                plugin_results = install_plugins_to_site(wp_url, auser, apwd, pids)
                                success_plugins = sum(1 for r in plugin_results if r.get("status") == "success")
                                update_bg_task(sid, status="installed", 
                                    message=f"WordPress安装成功，{success_plugins}/{len(pids)}个插件安装成功")
                                logger.info(f"BG plugins for {surl}: {plugin_results}")
                            except Exception as pe:
                                logger.warning(f"BG plugin install error for {surl}: {pe}")
                                update_bg_task(sid, status="installed", 
                                    message=f"WordPress安装成功，插件安装失败: {str(pe)[:60]}")
                    except Exception as e:
                        update_bg_task(sid, status="failed", message=str(e)[:100])
                        logger.error(f"BG WP install error for {surl}: {e}")

                create_bg_task(site_id_for_bg, "wp_install", status="installing", message="WordPress安装中...")
                bg_thread = threading.Thread(
                    target=_bg_wp_install,
                    args=(site_id_for_bg, container_name, f"http://{domain}", site_name,
                          default_admin, default_password, f"admin@{domain}", port, plugin_ids),
                    daemon=True,
                )
                bg_thread.start()
                logger.info(f"Started background WP install for {domain} (site_id={site_id_for_bg})")

                # Plugin results will be populated by the background thread
                plugin_results = []

                results.append({
                    "domain": domain,
                    "status": "success",
                    "port": port,
                    "site_id": site["id"] if site else None,
                    "panel_app_install_id": app_install_id,
                    "panel_website_id": panel_website_id,
                    "container_name": container_name,
                    "website_created": website_result.get("code") == 200 if website_result else False,
                    "wp_installed": False,  # Will be updated by background task
                    "wp_install_status": "installing",
                    "plugin_results": plugin_results,
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
        except Exception as e:
            logger.error(f"Batch create failed: {e}")
            return jsonify({"code": 500, "message": f"批量创建失败: {str(e)[:100]}"}), 500

    # ---- Global Config ----

    @app.route("/api/config", methods=["GET"])
    @jwt_required()
    def get_config():
        try:
            cfg = get_global_config()
            # Parse JSON values
            for key in ["default_plugins", "default_themes"]:
                if key in cfg:
                    try:
                        cfg[key] = json.loads(cfg[key])
                    except (json.JSONDecodeError, TypeError):
                        cfg[key] = []
            return jsonify({"code": 200, "data": cfg})
        except Exception as e:
            logger.error(f"Failed to get config: {e}")
            return jsonify({"code": 500, "message": f"获取配置失败: {str(e)[:100]}"}), 500

    @app.route("/api/config", methods=["PUT"])
    @jwt_required()
    def save_config():
        try:
            data = request.get_json(silent=True) or {}
            for key, value in data.items():
                update_global_config(key, value)
            return jsonify({"code": 200, "message": "Config saved"})
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return jsonify({"code": 500, "message": f"保存配置失败: {str(e)[:100]}"}), 500

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

    @app.route("/api/panel/test-wp-install", methods=["POST"])
    @jwt_required()
    def test_wp_install():
        """Test WordPress auto-install on a specific port."""
        data = request.get_json(silent=True) or {}
        port = data.get("port")
        domain = data.get("domain", "test.local")
        if not port:
            return jsonify({"code": 400, "message": "port required"}), 400
        result = auto_install_wordpress(
            container_name="",
            site_url=f"http://{domain}",
            site_title=domain,
            admin_user="admin",
            admin_password="Test123456!",
            admin_email=f"admin@{domain}",
            port=port,
        )
        return jsonify({"code": 200, "data": result})

    @app.route("/api/wordpress/install-status/<site_id>", methods=["GET"])
    @jwt_required()
    def wp_install_status(site_id):
        """Check WordPress installation status for a site."""
        task = get_bg_task(site_id)
        if task:
            return jsonify({"code": 200, "data": {
                "site_id": site_id,
                "status": task.get("status", "unknown"),
                "message": task.get("message", ""),
            }})
        return jsonify({"code": 200, "data": {
            "site_id": site_id,
            "status": "unknown",
            "message": "未找到安装任务",
        }})

    # ---- Plugins ----

    @app.route("/api/plugins", methods=["GET"])
    @jwt_required()
    def get_plugins():
        try:
            plugins = list_plugins()
            return jsonify({"code": 200, "data": plugins})
        except Exception as e:
            logger.error(f"Failed to list plugins: {e}")
            return jsonify({"code": 500, "message": f"获取插件列表失败: {str(e)[:100]}"}), 500

    @app.route("/api/plugins", methods=["POST"])
    @jwt_required()
    def upload_plugin():
        """Upload a WordPress plugin .zip file."""
        try:
            if "file" not in request.files:
                return jsonify({"code": 400, "message": "未找到上传文件"}), 400

            file = request.files["file"]
            if not file.filename.endswith(".zip"):
                return jsonify({"code": 400, "message": "仅支持 .zip 格式的插件文件"}), 400

            # Save file
            plugin_dir = os.path.join(os.path.dirname(__file__), "plugins")
            os.makedirs(plugin_dir, exist_ok=True)

            filename = file.filename
            file_path = os.path.join(plugin_dir, filename)

            # Avoid overwrite
            if os.path.exists(file_path):
                name, ext = os.path.splitext(filename)
                file_path = os.path.join(plugin_dir, f"{name}_{int(time.time())}{ext}")

            file.save(file_path)
            file_size = os.path.getsize(file_path)

            # Extract plugin name from zip (look for main PHP file)
            plugin_name = os.path.splitext(os.path.basename(file_path))[0]
            description = request.form.get("description", "")

            plugin = create_plugin({
                "name": plugin_name,
                "filename": os.path.basename(file_path),
                "file_path": file_path,
                "file_size": file_size,
                "description": description,
            })

            return jsonify({"code": 200, "data": plugin}), 201
        except Exception as e:
            logger.error(f"Failed to upload plugin: {e}")
            return jsonify({"code": 500, "message": f"上传插件失败: {str(e)[:100]}"}), 500

    @app.route("/api/plugins/<plugin_id>", methods=["DELETE"])
    @jwt_required()
    def remove_plugin(plugin_id):
        try:
            delete_plugin(plugin_id)
            return jsonify({"code": 200, "message": "插件已删除"})
        except Exception as e:
            logger.error(f"Failed to delete plugin {plugin_id}: {e}")
            return jsonify({"code": 500, "message": f"删除插件失败: {str(e)[:100]}"}), 500

    @app.route("/api/plugins/<plugin_id>/toggle", methods=["POST"])
    @jwt_required()
    def toggle_plugin(plugin_id):
        """Toggle plugin enabled/disabled."""
        try:
            plugin = get_plugin(plugin_id)
            if not plugin:
                return jsonify({"code": 404, "message": "插件不存在"}), 404
            from models import get_db as _get_db
            conn = _get_db()
            try:
                new_state = 0 if plugin["enabled"] else 1
                conn.execute("UPDATE plugins SET enabled = ? WHERE id = ?", (new_state, plugin_id))
                conn.commit()
            finally:
                conn.close()
            return jsonify({"code": 200, "data": {"enabled": bool(new_state)}})
        except Exception as e:
            logger.error(f"Failed to toggle plugin {plugin_id}: {e}")
            return jsonify({"code": 500, "message": f"切换插件状态失败: {str(e)[:100]}"}), 500
