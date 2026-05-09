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
from werkzeug.utils import secure_filename

from config import config
from models import (
    create_bg_task,
    create_cf_account,
    create_plugin,
    create_site,
    delete_cf_account,
    delete_plugin,
    delete_site,
    get_bg_task,
    get_bg_task_by_site,
    get_cf_account,
    get_db,
    get_default_cf_account,
    get_enabled_plugins,
    get_global_config,
    get_plugin,
    get_site,
    init_db,
    list_cf_accounts,
    list_plugins,
    list_sites,
    set_default_cf_account,
    update_bg_task,
    update_global_config,
    update_site,
    update_site_fields,
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


def install_themes_to_site(site_url, admin_user, admin_password, theme_ids):
    """Install themes to a WordPress site via wp-admin HTTP API.
    
    Steps:
    1. Login to wp-admin via HTTP
    2. Get upload nonce from theme-install page
    3. Upload each theme zip via wp-admin/update.php
    4. Activate the last theme via wp-admin/themes.php
    """
    import re as _re
    
    results = []
    
    try:
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
            return [{"theme": t, "status": "error", "message": "WordPress登录失败"} for t in theme_ids]

        last_theme_stylesheet = None
        
        for tid in theme_ids:
            conn = get_db()
            try:
                theme = conn.execute("SELECT * FROM themes WHERE id = ?", (tid,)).fetchone()
            finally:
                conn.close()
            if not theme or not theme["file_path"] or not os.path.isfile(theme["file_path"]):
                results.append({"theme": tid, "status": "error", "message": "主题文件不存在"})
                continue

            theme_name = theme["name"]
            theme_filename = theme["filename"]

            try:
                # Get upload nonce from theme-install page
                upload_page = session.get(
                    f"{site_url}/wp-admin/theme-install.php?upload",
                    timeout=15,
                )
                nonce_match = _re.search(r'_wpnonce["\']\s*value=["\']([^"\']+)', upload_page.text)
                if not nonce_match:
                    results.append({"theme": theme_name, "status": "error", "message": "无法获取上传nonce"})
                    continue
                nonce = nonce_match.group(1)

                # Upload theme zip
                with open(theme["file_path"], "rb") as f:
                    upload_resp = session.post(
                        f"{site_url}/wp-admin/update.php?action=upload-theme",
                        files={"themezip": (theme_filename, f, "application/zip")},
                        data={"_wpnonce": nonce},
                        timeout=120,
                    )

                if upload_resp.status_code != 200:
                    results.append({"theme": theme_name, "status": "error", "message": f"上传失败: HTTP {upload_resp.status_code}"})
                    continue

                # Find activation link
                act_match = _re.search(
                    r'action=activate[^"]*stylesheet=([^"&]+)[^"]*_wpnonce=([a-f0-9]+)',
                    upload_resp.text,
                )
                if act_match:
                    stylesheet = act_match.group(1)
                    act_nonce = act_match.group(2)
                    last_theme_stylesheet = stylesheet
                    
                    # Activate the theme
                    act_resp = session.get(
                        f"{site_url}/wp-admin/themes.php?action=activate&stylesheet={stylesheet}&_wpnonce={act_nonce}",
                        timeout=30,
                        allow_redirects=True,
                    )
                    if act_resp.status_code == 200:
                        results.append({"theme": theme_name, "status": "success", "message": "主题已安装并启用"})
                    else:
                        results.append({"theme": theme_name, "status": "success", "message": "主题已安装，启用需确认"})
                elif "已安装" in upload_resp.text or "already" in upload_resp.text.lower() or "Installed" in upload_resp.text:
                    # Theme already exists, try to extract stylesheet from page
                    existing_match = _re.search(r'stylesheet=([^"&]+)', upload_resp.text)
                    if existing_match:
                        last_theme_stylesheet = existing_match.group(1)
                    results.append({"theme": theme_name, "status": "success", "message": "主题已存在"})
                else:
                    results.append({"theme": theme_name, "status": "error", "message": "上传后未找到激活链接"})

            except http_requests.Timeout:
                results.append({"theme": theme_name, "status": "error", "message": "请求超时"})
            except Exception as e:
                results.append({"theme": theme_name, "status": "error", "message": str(e)[:100]})

        # Activate the last theme if not yet activated
        if last_theme_stylesheet and results:
            last_result = results[-1]
            if last_result.get("status") == "success" and "启用" not in last_result.get("message", ""):
                try:
                    themes_page = session.get(f"{site_url}/wp-admin/themes.php", timeout=15)
                    act_nonce = _re.search(
                        rf'stylesheet={_re.escape(last_theme_stylesheet)}[^"]*_wpnonce=([a-f0-9]+)',
                        themes_page.text,
                    )
                    if act_nonce:
                        session.get(
                            f"{site_url}/wp-admin/themes.php?action=activate&stylesheet={last_theme_stylesheet}&_wpnonce={act_nonce.group(1)}",
                            timeout=30,
                            allow_redirects=True,
                        )
                        last_result["message"] = "主题已安装并启用"
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Theme install session error: {e}")
        results = [{"theme": t, "status": "error", "message": f"会话错误: {str(e)[:80]}"} for t in theme_ids]

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


def _get_cf_credentials(account_id=None):
    """Get Cloudflare credentials. Supports multi-account via account_id.

    If account_id is given, uses that specific account.
    Otherwise uses the default account.
    Falls back to old global_config EAV format for backward compatibility.
    """
    if account_id:
        acct = get_cf_account(account_id)
        if acct:
            return {"api_token": acct["api_token"] or None,
                    "api_email": acct["api_email"] or None,
                    "api_key": acct["api_key"] or None}

    # Try new accounts table first
    acct = get_default_cf_account()
    if acct and (acct.get("api_token") or acct.get("api_email")):
        return {"api_token": acct["api_token"] or None,
                "api_email": acct["api_email"] or None,
                "api_key": acct["api_key"] or None}

    # Fallback to old global_config format
    conn = get_db()
    try:
        creds = {}
        for key, field in [("cf_api_token", "api_token"), ("cf_api_email", "api_email"), ("cf_api_key", "api_key")]:
            row = conn.execute("SELECT config_value FROM global_config WHERE config_key = ?", (key,)).fetchone()
            val = row["config_value"].strip() if row and row["config_value"] and row["config_value"].strip() else None
            creds[field] = val
        return creds
    finally:
        conn.close()

def _get_cf_token(account_id=None):
    """Get Cloudflare API token."""
    return _get_cf_credentials(account_id).get("api_token")

def _get_config_value(key, default=None):
    """Get a config value from global_config (EAV format)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT config_value FROM global_config WHERE config_key = ?", (key,)).fetchone()
        return row["config_value"] if row and row["config_value"] else default
    finally:
        conn.close()

def _set_config_value(key, value):
    """Set a config value in global_config (EAV format)."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO global_config (config_key, config_value, updated_at) VALUES (?, ?, ?) ON CONFLICT(config_key) DO UPDATE SET config_value = ?, updated_at = ?",
            (key, value, datetime.utcnow().isoformat(), value, datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


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
                    panel_items = (panel_resp.get("data") or {}).get("items") or []
                    panel_sites = {s["id"]: s for s in panel_items}
                    for site in sites:
                        pwid = site.get("panel_website_id")
                        if pwid and pwid in panel_sites:
                            ps = panel_sites[pwid]
                            site["panel_status"] = ps.get("status", "")
                            site["panel_protocol"] = ps.get("protocol", "")
                            site["panel_alias"] = ps.get("alias", "")
                        elif pwid:
                            # Panel website no longer exists
                            site["panel_status"] = "deleted"
                        else:
                            site["panel_status"] = site.get("panel_status") or "unlinked"
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

    @app.route("/api/sites/<int:site_id>", methods=["GET"])
    @jwt_required()
    def get_site_detail(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "Site not found"}), 404
            # Enrich with 1Panel data
            try:
                pwid = site.get("panel_website_id")
                if pwid:
                    ws_resp = panel_client.search_websites(name=site.get("site_name", ""))
                    if ws_resp.get("code") == 200:
                        for w in (ws_resp.get("data") or {}).get("items") or []:
                            if w.get("id") == pwid:
                                site["panel_status"] = w.get("status")
                                site["panel_protocol"] = w.get("protocol")
                                site["panel_alias"] = w.get("alias")
                                site["panel_domains"] = w.get("domains", [])
                                site["panel_site_path"] = w.get("sitePath", "")
                                break
                paid = site.get("panel_app_install_id")
                if paid:
                    app_resp = panel_client.search_installed_apps(name="")
                    if app_resp.get("code") == 200:
                        for a in (app_resp.get("data") or {}).get("items") or []:
                            if a.get("id") == paid:
                                site["panel_app_status"] = a.get("status")
                                site["panel_app_version"] = a.get("version")
                                site["panel_app_name"] = a.get("name")
                                site["panel_container"] = a.get("container")
                                site["panel_http_port"] = a.get("httpPort")
                                break
            except Exception as e:
                logger.warning(f"Failed to enrich site detail from panel: {e}")
            return jsonify({"code": 200, "data": site})
        except Exception as e:
            logger.error(f"Failed to get site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"获取站点详情失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>", methods=["PUT"])
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

    @app.route("/api/sites/<int:site_id>", methods=["DELETE"])
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

            # Clean up the independently-created database
            db_name = site.get("db_name")
            db_service = site.get("db_service") or "mariadb"
            if db_name:
                try:
                    db_resp = panel_client.search_databases(name=db_name)
                    if db_resp.get("code") == 200:
                        db_items = (db_resp.get("data") or {}).get("items") or []
                        for d in db_items:
                            if d.get("name") == db_name:
                                panel_client.delete_database(
                                    d.get("id"), db_type=db_service,
                                    delete_user=True, force_delete=True,
                                )
                                logger.info(f"Deleted 1Panel database {db_name}")
                                break
                except Exception as e:
                    logger.warning(f"Failed to delete database {db_name}: {e}")

            delete_site(site_id)
            return jsonify({"code": 200, "message": "站点已删除"})
        except Exception as e:
            logger.error(f"Failed to delete site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"删除站点失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/fix-website", methods=["POST"])
    @jwt_required()
    def fix_site_website(site_id):
        """Fix a site that has a WP app but no 1Panel website (OpenResty deployment).
        
        This creates the missing 1Panel deployment website and links it to the
        existing WP application, then updates the local DB.
        """
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            # Already has a website
            if site.get("panel_website_id"):
                return jsonify({"code": 400, "message": "站点已有1Panel网站，无需修复"})

            app_install_id = site.get("panel_app_install_id")
            if not app_install_id:
                return jsonify({"code": 400, "message": "站点缺少1Panel应用ID，无法修复"})

            alias = site.get("nginx_alias") or site["site_name"].replace(".", "-")
            domain = site["site_name"]
            port = site.get("port", 8081)

            # First, check if a website with this alias/domain already exists in 1Panel
            existing_website_id = None
            try:
                ws = panel_client.search_websites(name=domain)
                if ws.get("code") == 200:
                    items = ws.get("data", {}).get("items") or []
                    for w in items:
                        if w.get("alias") == alias or w.get("primaryDomain") == domain:
                            existing_website_id = w.get("id")
                            logger.info(f"Fix-website: Found existing website id={existing_website_id} for {domain}")
                            break
            except Exception as e:
                logger.warning(f"Fix-website: search_websites failed: {e}")

            if existing_website_id:
                # Website already exists in 1Panel, just update our DB
                update_site_fields(site_id, {"panel_website_id": existing_website_id})
                return jsonify({
                    "code": 200,
                    "message": "1Panel网站已存在，已自动关联",
                    "data": {"panel_website_id": existing_website_id, "alias": alias},
                })

            # Ensure website group exists
            group_id = panel_client.ensure_website_group()

            # Create the deployment website
            logger.info(f"Fix-website: Creating deployment website for {domain} (app_install_id={app_install_id})")
            result = panel_client.create_website(
                primary_domain=domain,
                alias=alias,
                app_type="installed",
                app_install_id=app_install_id,
                website_group_id=group_id,
                enable_ipv6=False,
            )
            logger.info(f"Fix-website: create_website response: code={result.get('code')}, message={result.get('message','')[:200]}")

            # If alias conflict, try with a unique alias
            if result.get("code") != 200 and "标识已存在" in str(result.get("message", "")):
                for suffix in range(2, 5):
                    unique_alias = f"{alias}-{suffix}"
                    logger.info(f"Fix-website: Retrying with alias={unique_alias}")
                    result = panel_client.create_website(
                        primary_domain=domain,
                        alias=unique_alias,
                        app_type="installed",
                        app_install_id=app_install_id,
                        website_group_id=group_id,
                        enable_ipv6=False,
                    )
                    if result.get("code") == 200:
                        alias = unique_alias
                        break

            if result.get("code") != 200:
                return jsonify({"code": 500, "message": f"创建1Panel网站失败: {result.get('message', '未知错误')[:100]}"})

            # Find the website ID
            website_id = None
            ws = panel_client.search_websites(name=domain)
            if ws.get("code") == 200:
                items = ws.get("data", {}).get("items") or []
                for w in items:
                    if w.get("alias") == alias or w.get("primaryDomain") == domain:
                        website_id = w.get("id")
                        break

            # Update local DB
            update_site_fields(site_id, {"panel_website_id": website_id, "nginx_alias": alias})

            return jsonify({
                "code": 200,
                "message": "1Panel网站已创建并关联",
                "data": {"panel_website_id": website_id, "alias": alias},
            })
        except Exception as e:
            logger.error(f"Fix-website failed for {site_id}: {e}")
            return jsonify({"code": 500, "message": f"修复失败: {str(e)[:100]}"}), 500

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
                app_install_params=data.get("appInstallParams"),
                services=data.get("services"),
                website_group_id=data.get("webSiteGroupID", 1),
                remark=data.get("remark", ""),
                enable_ipv6=data.get("enableIPV6", False),
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

                alias = domain
                site_name = domain
                port = find_available_port(base_port)

                # Generate DB credentials
                import string
                import random
                db_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
                db_name = f"wp_{db_suffix}"
                db_user = f"wp_{db_suffix}"
                db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=16))

                # Create a placeholder site in local DB immediately
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
                    "port": port,
                    "db_name": db_name,
                    "db_service": db_service,
                })
                site_id_for_bg = site["id"] if site else 0

                # Initialize bg task — the full deployment runs in background
                bg_task_id = create_bg_task(site_id_for_bg, "wp_install", status="installing",
                               message="1Panel正在创建数据库...")

                # ---- Background thread: full deployment pipeline ----
                def _bg_deploy(task_id, sid, s_alias, s_domain, s_port, s_db_name, s_db_user, s_db_pass,
                                s_app_detail_id, s_app_id, s_db_service, s_admin, s_password, s_plugin_ids,
                                s_group_id):
                    """Full deployment pipeline in background with real-time status updates."""
                    # Push Flask application context for this thread
                    with app.app_context():
                        _bg_deploy_inner(task_id, sid, s_alias, s_domain, s_port, s_db_name, s_db_user, s_db_pass,
                                         s_app_detail_id, s_app_id, s_db_service, s_admin, s_password, s_plugin_ids,
                                         s_group_id)

                def _bg_deploy_inner(task_id, sid, s_alias, s_domain, s_port, s_db_name, s_db_user, s_db_pass,
                                     s_app_detail_id, s_app_id, s_db_service, s_admin, s_password, s_plugin_ids,
                                     s_group_id):
                    """Inner deployment logic (runs inside Flask app context)."""
                    container_name = None
                    app_install_id = None
                    panel_website_id = None

                    def _rollback_deploy(db_name, app_install_id_to_delete=None):
                        """清理部署失败时已创建的资源（数据库和应用）。"""
                        if app_install_id_to_delete:
                            try:
                                panel_client.operate_installed(
                                    app_install_id_to_delete, "delete",
                                    force_delete=True, delete_backup=True, delete_db=False,
                                )
                                logger.info(f"Rollback: deleted app install {app_install_id_to_delete}")
                            except Exception as re:
                                logger.warning(f"Rollback: failed to delete app: {re}")
                        if db_name:
                            try:
                                db_resp = panel_client.search_databases(name=db_name)
                                if db_resp.get("code") == 200:
                                    db_items = (db_resp.get("data") or {}).get("items") or []
                                    for d in db_items:
                                        if d.get("name") == db_name:
                                            panel_client.delete_database(
                                                d.get("id"), db_type=s_db_service,
                                                delete_user=True, force_delete=True,
                                            )
                                            logger.info(f"Rollback: deleted database {db_name}")
                                            break
                            except Exception as re:
                                logger.warning(f"Rollback: failed to delete DB: {re}")

                    try:
                        # === Step 1: Create database ===
                        update_bg_task(task_id, status="installing", message="1Panel正在创建数据库...")
                        db_created = False
                        try:
                            db_resp = panel_client.create_database(
                                name=s_db_name, db_type=s_db_service, username=s_db_user,
                                password=s_db_pass, permission="%", format_str="utf8mb4",
                            )
                            db_created = db_resp.get("code") == 200
                        except Exception as e:
                            logger.warning(f"DB creation failed for {s_domain}: {e}")

                        if not db_created:
                            update_bg_task(task_id, status="failed",
                                           message=f"创建数据库 {s_db_name} 失败，请检查1Panel数据库服务")
                            return

                        # === Step 2: Create website + install WordPress app (one-step via appType=new) ===
                        update_bg_task(task_id, status="installing",
                                       message="1Panel正在安装WordPress并创建网站...")
                        install_params = {
                            "PANEL_DB_TYPE": s_db_service,
                            "PANEL_DB_HOST": s_db_service,
                            "PANEL_DB_NAME": s_db_name,
                            "PANEL_DB_USER": s_db_user,
                            "PANEL_DB_USER_PASSWORD": s_db_pass,
                            "PANEL_APP_PORT_HTTP": str(s_port),
                        }

                        # Try one-step creation (appType=new) first
                        website_result = None
                        for _attempt in range(3):
                            try:
                                logger.info(f"Step2: One-step create website+app for {s_domain} (attempt {_attempt+1}/3)")
                                website_result = panel_client.create_website(
                                    primary_domain=s_domain,
                                    alias=s_alias,
                                    app_type="new",
                                    app_detail_id=s_app_detail_id,
                                    app_id=s_app_id,
                                    app_install_params=install_params,
                                    services={s_db_service: s_db_service},
                                    website_group_id=s_group_id,
                                    enable_ipv6=False,
                                )
                                logger.info(f"Step2: create_website response: code={website_result.get('code')}, message={website_result.get('message','')[:200]}")
                                if website_result.get("code") == 200:
                                    break
                                # If alias conflict, try with unique suffix
                                if "标识已存在" in str(website_result.get("message", "")):
                                    unique_alias = f"{s_alias}-{_attempt+1}"
                                    logger.warning(f"Step2: Alias conflict, trying {unique_alias}")
                                    website_result = panel_client.create_website(
                                        primary_domain=s_domain,
                                        alias=unique_alias,
                                        app_type="new",
                                        app_detail_id=s_app_detail_id,
                                        app_id=s_app_id,
                                        app_install_params=install_params,
                                        services={s_db_service: s_db_service},
                                        website_group_id=s_group_id,
                                        enable_ipv6=False,
                                    )
                                    logger.info(f"Step2: Retry alias={unique_alias}: code={website_result.get('code')}")
                                    if website_result.get("code") == 200:
                                        s_alias = unique_alias
                                        break
                            except Exception as e:
                                logger.error(f"Step2: One-step create exception (attempt {_attempt+1}): {e}")
                                website_result = {"code": 500, "message": str(e)[:200]}
                            if _attempt < 2:
                                time.sleep(5)

                        if website_result and website_result.get("code") == 200:
                            # Wait for app to start
                            time.sleep(8)

                            # Find the website and app IDs
                            try:
                                ws = panel_client.search_websites(name=s_domain)
                                if ws.get("code") == 200:
                                    items = ws.get("data", {}).get("items") or []
                                    for w in items:
                                        if w.get("alias") == s_alias or w.get("primaryDomain") == s_domain:
                                            panel_website_id = w.get("id")
                                            app_install_id = w.get("appInstallId")
                                            logger.info(f"Step2: Found website id={panel_website_id}, appInstallId={app_install_id} for {s_domain}")
                                            break
                            except Exception as e:
                                logger.warning(f"Step2: search_websites failed: {e}")

                            # If we didn't get appInstallId from website, search apps
                            if not app_install_id:
                                try:
                                    new_installed = panel_client.search_installed_apps(name=s_alias)
                                    if new_installed.get("code") == 200:
                                        new_app = next(
                                            (a for a in new_installed.get("data", {}).get("items", [])
                                             if a.get("name") == s_alias), None,
                                        )
                                        if new_app:
                                            app_install_id = new_app.get("id")
                                            container_name = new_app.get("container", "")
                                except Exception:
                                    pass

                            update_bg_task(task_id, status="deploying",
                                           message="1Panel已创建网站和WordPress应用，正在等待就绪...")
                        else:
                            # One-step creation failed, fall back to two-step approach
                            error_msg = website_result.get('message', '未知错误') if website_result else '无响应'
                            logger.warning(f"Step2: One-step creation failed ({error_msg}), falling back to two-step")

                            # Fallback Step 2a: Install app first
                            update_bg_task(task_id, status="installing",
                                           message="1Panel正在安装WordPress应用...")
                            try:
                                install_resp = panel_client.install_app(
                                    app_detail_id=s_app_detail_id, name=s_alias,
                                    params=install_params, services={s_db_service: s_db_service},
                                    advanced=True, allow_port=True,
                                )
                            except Exception as e:
                                update_bg_task(task_id, status="failed", message=f"安装WordPress应用失败: {str(e)[:80]}")
                                _rollback_deploy(s_db_name)
                                return

                            if install_resp.get("code") != 200:
                                update_bg_task(task_id, status="failed",
                                               message=f"安装WordPress应用失败: {install_resp.get('message', '未知错误')[:80]}")
                                _rollback_deploy(s_db_name)
                                return

                            time.sleep(5)

                            # Get installed app ID
                            try:
                                new_installed = panel_client.search_installed_apps(name=s_alias)
                                if new_installed.get("code") == 200:
                                    new_app = next(
                                        (a for a in new_installed.get("data", {}).get("items", [])
                                         if a.get("name") == s_alias), None,
                                    )
                                    if new_app:
                                        app_install_id = new_app.get("id")
                                        container_name = new_app.get("container", "")
                            except Exception:
                                pass

                            # Fallback Step 2b: Create deployment website
                            update_bg_task(task_id, status="deploying",
                                           message="1Panel正在部署网站...")
                            for _attempt2 in range(3):
                                try:
                                    website_result = panel_client.create_website(
                                        primary_domain=s_domain,
                                        alias=s_alias,
                                        app_type="installed",
                                        app_install_id=app_install_id,
                                        website_group_id=s_group_id,
                                        enable_ipv6=False,
                                    )
                                    if website_result.get("code") == 200:
                                        break
                                except Exception as e:
                                    logger.error(f"Step2b: create_website exception: {e}")
                                time.sleep(5)

                            if website_result and website_result.get("code") == 200:
                                try:
                                    ws = panel_client.search_websites(name=s_domain)
                                    if ws.get("code") == 200:
                                        items = ws.get("data", {}).get("items") or []
                                        for w in items:
                                            if w.get("alias") == s_alias or w.get("primaryDomain") == s_domain:
                                                panel_website_id = w.get("id")
                                                break
                                except Exception:
                                    pass
                            else:
                                # Step 2b failed — fall back to manual nginx proxy config
                                error_msg = website_result.get('message', '未知错误') if website_result else '无响应'
                                logger.warning(f"Step2b: create_website failed ({error_msg}), falling back to nginx proxy")
                                update_bg_task(task_id, status="deploying",
                                               message="1Panel网站API不可用，正在手动配置nginx反向代理...")
                                try:
                                    nginx_resp = panel_client.create_nginx_proxy_config(
                                        alias=s_alias, domain=s_domain, port=s_port,
                                    )
                                    if nginx_resp.get("code") == 200:
                                        logger.info(f"Step2c: nginx proxy config created for {s_domain}")
                                    else:
                                        logger.warning(f"Step2c: nginx proxy config partial: {nginx_resp.get('message','')[:100]}")
                                    # Continue with the flow — app is installed and nginx proxy is configured
                                except Exception as e:
                                    logger.error(f"Step2c: nginx proxy creation failed: {e}")
                                    update_bg_task(task_id, status="failed",
                                                   message=f"创建nginx反向代理失败: {str(e)[:80]}")
                                    _rollback_deploy(s_db_name, app_install_id)
                                    return

                        # Update local DB with panel IDs
                        try:
                            update_site_fields(sid, {
                                "panel_website_id": panel_website_id,
                                "panel_app_install_id": app_install_id,
                                "panel_app_detail_id": s_app_detail_id,
                                "nginx_alias": s_alias,
                            })
                        except Exception:
                            pass

                        # === Step 4: Wait for WordPress to be ready, then auto-install ===
                        update_bg_task(task_id, status="installing",
                                       message="WordPress正在启动，等待就绪...")

                        # Wait for WordPress container to fully start
                        from config import config as app_config
                        wp_host = app_config.PANEL_SERVER_IP
                        wp_check_url = f"http://{wp_host}:{s_port}/"
                        logger.info(f"WP readiness check: url={wp_check_url}")
                        max_wait = 120  # seconds
                        waited = 0
                        wp_ready = False
                        while waited < max_wait:
                            try:
                                check_resp = http_requests.get(
                                    wp_check_url,
                                    timeout=5, allow_redirects=False,
                                )
                                logger.info(f"WP check: status={check_resp.status_code}, waited={waited}s")
                                if check_resp.status_code in (200, 301, 302):
                                    wp_ready = True
                                    break
                            except Exception as ce:
                                logger.info(f"WP check failed: {ce}, waited={waited}s")
                            time.sleep(5)
                            waited += 5

                        if not wp_ready:
                            update_bg_task(task_id, status="failed",
                                           message=f"WordPress应用启动超时({max_wait}秒)，请手动检查")
                            return

                        # === Step 5: Auto-complete WordPress installation ===
                        update_bg_task(task_id, status="installing",
                                       message="WordPress正在初始化配置...")
                        result = auto_install_wordpress(
                            container_name=container_name or "",
                            site_url=f"http://{s_domain}",
                            site_title=s_domain,
                            admin_user=s_admin,
                            admin_password=s_password,
                            admin_email=f"admin@{s_domain}",
                            port=s_port,
                        )

                        if result.get("success"):
                            # === Step 6: Install plugins ===
                            if s_plugin_ids:
                                update_bg_task(task_id, status="installing",
                                               message=f"WordPress已安装，正在安装 {len(s_plugin_ids)} 个插件...")
                                try:
                                    wp_url = f"http://{wp_host}:{s_port}"
                                    plugin_results = install_plugins_to_site(
                                        wp_url, s_admin, s_password, s_plugin_ids)
                                    ok = sum(1 for r in plugin_results if r.get("status") == "success")
                                    update_bg_task(task_id, status="installed",
                                                   message=f"部署完成！WordPress已安装，{ok}/{len(s_plugin_ids)} 个插件安装成功")
                                except Exception as pe:
                                    update_bg_task(task_id, status="installed",
                                                   message=f"部署完成！WordPress已安装，插件安装失败: {str(pe)[:60]}")
                            else:
                                update_bg_task(task_id, status="installed",
                                               message="部署完成！1Panel(OpenResty) + WordPress 安装成功")
                        else:
                            update_bg_task(task_id, status="failed",
                                           message=f"WordPress初始化失败: {result.get('message', '未知错误')[:80]}")

                    except Exception as e:
                        update_bg_task(task_id, status="failed", message=f"部署异常: {str(e)[:100]}")
                        _rollback_deploy(s_db_name, app_install_id)
                        logger.error(f"BG deploy error for {s_domain}: {e}")

                # Get group ID before starting bg thread
                # Use website_group_id from frontend request, or auto-detect
                website_group_id = data.get("website_group_id")
                if website_group_id:
                    group_id = website_group_id
                else:
                    try:
                        group_id = panel_client.ensure_website_group()
                    except Exception:
                        group_id = 1

                bg_thread = threading.Thread(
                    target=_bg_deploy,
                    args=(bg_task_id, site_id_for_bg, alias, domain, port, db_name, db_user, db_pass,
                          app_detail_id, app_id, db_service, default_admin, default_password,
                          plugin_ids, group_id),
                    daemon=True,
                )
                bg_thread.start()
                logger.info(f"Started background deployment for {domain} (site_id={site_id_for_bg}, task_id={bg_task_id})")

                results.append({
                    "domain": domain,
                    "status": "success",
                    "port": port,
                    "site_id": site["id"] if site else None,
                    "wp_install_status": "installing",
                    "wp_install_message": "1Panel正在创建数据库...",
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

    @app.route("/api/panel/sync", methods=["POST"])
    @jwt_required()
    def panel_sync():
        """Sync local sites with 1Panel actual state.

        For each local site:
        - Verify the 1Panel website still exists; if not, clear panel IDs
        - Verify the 1Panel app install still exists; if not, clear panel IDs
        - If 1Panel has websites/apps not in our DB, optionally import them

        Also reconciles:
        - Orphaned WordPress apps (installed but no website in 1Panel)
        - Sites where the 1Panel website was deleted externally
        """
        results = {"updated": 0, "cleared": 0, "imported": 0, "errors": []}
        try:
            # Get all 1Panel data
            ws_resp = panel_client.search_websites()
            panel_websites = {}
            if ws_resp.get("code") == 200:
                for w in (ws_resp.get("data") or {}).get("items") or []:
                    panel_websites[w.get("id")] = w

            # Get ALL installed apps (don't filter by type — WordPress installed apps
            # may have different appType values depending on how they were installed)
            app_resp = panel_client.search_installed_apps(name="")
            panel_apps = {}
            wp_apps = []
            if app_resp.get("code") == 200:
                for a in (app_resp.get("data") or {}).get("items") or []:
                    panel_apps[a.get("id")] = a
                    if a.get("appKey") == "wordpress":
                        wp_apps.append(a)

            # Check each local site
            sites = list_sites()
            for site in sites:
                pwid = site.get("panel_website_id")
                paid = site.get("panel_app_install_id")
                updates = {}

                # Check if 1Panel website still exists
                if pwid:
                    if pwid not in panel_websites:
                        # Website gone from 1Panel
                        updates["panel_website_id"] = None
                        updates["panel_app_install_id"] = None
                        results["cleared"] += 1
                        logger.info(f"Sync: cleared stale panel IDs for site {site.get('id')} ({site.get('site_name')})")

                # Check if 1Panel app still exists
                if paid and paid not in panel_apps and "panel_app_install_id" not in updates:
                    updates["panel_app_install_id"] = None
                    if "panel_website_id" not in updates:
                        updates["panel_website_id"] = None
                    results["cleared"] += 1
                    logger.info(f"Sync: cleared stale panel app ID for site {site.get('id')}")

                # If site has no panel IDs but has a matching 1Panel website, link it
                if not pwid and not paid:
                    alias = site.get("nginx_alias") or site.get("site_name", "").replace(".", "-")
                    for pw in panel_websites.values():
                        if pw.get("alias") == alias or pw.get("primaryDomain") == site.get("site_name"):
                            updates["panel_website_id"] = pw.get("id")
                            updates["panel_app_install_id"] = pw.get("appInstallId")
                            results["updated"] += 1
                            logger.info(f"Sync: linked site {site.get('id')} to 1Panel website {pw.get('id')}")
                            break

                if updates:
                    try:
                        update_site_fields(site.get("id"), updates)
                    except Exception as e:
                        results["errors"].append(f"Site {site.get('id')}: {e}")

            # Check for WordPress apps in 1Panel that have NO website (orphaned)
            # These are the apps that WOULD show up in 1Panel's "已安装应用" dropdown
            linked_app_ids = set(w.get("appInstallId") for w in panel_websites.values() if w.get("appInstallId"))
            orphaned_wp_apps = [a for a in wp_apps if a.get("id") not in linked_app_ids]
            results["orphaned_wp_apps"] = len(orphaned_wp_apps)
            results["orphaned_wp_app_details"] = [
                {"id": a.get("id"), "name": a.get("name"), "status": a.get("status"), "httpPort": a.get("httpPort")}
                for a in orphaned_wp_apps
            ]

            # Import orphaned WordPress apps as new sites (if requested)
            import_orphans = (request.get_json(silent=True) or {}).get("import_orphans", False)
            if import_orphans and orphaned_wp_apps:
                for app in orphaned_wp_apps:
                    # Check if already in our DB
                    existing = False
                    for site in sites:
                        if site.get("panel_app_install_id") == app.get("id"):
                            existing = True
                            break
                    if not existing:
                        try:
                            domain = app.get("name", "").replace("-", ".")
                            port = app.get("httpPort", 8081)
                            new_site = create_site({
                                "site_name": domain,
                                "url": f"http://{domain}",
                                "port": port,
                                "admin_name": "admin",
                                "admin_password": "",
                                "panel_app_install_id": app.get("id"),
                                "panel_website_id": None,
                                "nginx_alias": app.get("name"),
                                "tag": "imported",
                            })
                            results["imported"] += 1
                            logger.info(f"Sync: imported orphaned WP app {app.get('id')} as site {new_site.get('id')}")
                        except Exception as e:
                            results["errors"].append(f"Import {app.get('name')}: {e}")

            # Detect orphaned databases (wp_* databases not linked to any local site)
            try:
                db_resp = panel_client.search_databases(name="wp_")
                orphaned_dbs = []
                if db_resp.get("code") == 200:
                    db_items = (db_resp.get("data") or {}).get("items") or []
                    known_db_names = set(site.get("db_name") for site in sites if site.get("db_name"))
                    for d in db_items:
                        d_name = d.get("name", "")
                        if d_name.startswith("wp_") and d_name not in known_db_names:
                            orphaned_dbs.append({"id": d.get("id"), "name": d_name, "type": d.get("type", "")})
                results["orphaned_databases"] = len(orphaned_dbs)
                results["orphaned_db_details"] = orphaned_dbs

                # Clean up orphaned databases if requested
                if import_orphans and orphaned_dbs:
                    cleaned_dbs = 0
                    for d in orphaned_dbs:
                        try:
                            db_type = d.get("type") or "mariadb"
                            panel_client.delete_database(
                                d.get("id"), db_type=db_type,
                                delete_user=True, force_delete=True,
                            )
                            cleaned_dbs += 1
                            logger.info(f"Sync: cleaned up orphaned database {d.get('name')}")
                        except Exception as de:
                            results["errors"].append(f"Cleanup DB {d.get('name')}: {de}")
                    if cleaned_dbs:
                        results["cleaned_databases"] = cleaned_dbs
            except Exception as de:
                logger.warning(f"Sync: orphaned DB detection failed: {de}")

            return jsonify({"code": 200, "data": results})
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return jsonify({"code": 500, "message": f"同步失败: {str(e)[:100]}"}), 500

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

    @app.route("/api/wordpress/install-status/<int:site_id>", methods=["GET"])
    @jwt_required()
    def wp_install_status(site_id):
        """Check WordPress installation status for a site."""
        task = get_bg_task_by_site(site_id)
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

    @app.route("/api/plugins/<int:plugin_id>", methods=["DELETE"])
    @jwt_required()
    def remove_plugin(plugin_id):
        try:
            delete_plugin(plugin_id)
            return jsonify({"code": 200, "message": "插件已删除"})
        except Exception as e:
            logger.error(f"Failed to delete plugin {plugin_id}: {e}")
            return jsonify({"code": 500, "message": f"删除插件失败: {str(e)[:100]}"}), 500

    @app.route("/api/plugins/<int:plugin_id>/toggle", methods=["POST"])
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

    # ---- Themes ----
    @app.route("/api/themes/upload", methods=["POST"])
    @jwt_required()
    def upload_theme():
        """Upload a WordPress theme .zip file."""
        try:
            if "file" not in request.files:
                return jsonify({"code": 400, "message": "未找到上传文件"}), 400
            f = request.files["file"]
            if not f.filename.endswith(".zip"):
                return jsonify({"code": 400, "message": "仅支持 .zip 格式的主题文件"}), 400

            theme_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes")
            os.makedirs(theme_dir, exist_ok=True)

            filename = secure_filename(f.filename)
            file_path = os.path.join(theme_dir, filename)
            f.save(file_path)
            file_size = os.path.getsize(file_path)

            # Store in DB
            conn = get_db()
            try:
                name = request.form.get("name", filename.replace(".zip", ""))
                conn.execute(
                    "INSERT INTO themes (name, filename, file_path, file_size, description) VALUES (?, ?, ?, ?, ?)",
                    (name, filename, file_path, file_size, request.form.get("description", "")),
                )
                conn.commit()
                theme_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            finally:
                conn.close()

            return jsonify({"code": 200, "data": {"id": theme_id, "name": name, "filename": filename, "file_size": file_size}})
        except Exception as e:
            logger.error(f"Failed to upload theme: {e}")
            return jsonify({"code": 500, "message": f"上传主题失败: {str(e)[:100]}"}), 500

    @app.route("/api/themes", methods=["GET"])
    @jwt_required()
    def list_themes():
        """List all uploaded themes."""
        try:
            conn = get_db()
            try:
                rows = conn.execute("SELECT * FROM themes ORDER BY id DESC").fetchall()
            finally:
                conn.close()
            return jsonify({"code": 200, "data": [dict(r) for r in rows]})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/themes/<int:theme_id>", methods=["DELETE"])
    @jwt_required()
    def delete_theme(theme_id):
        """Delete an uploaded theme."""
        try:
            conn = get_db()
            try:
                row = conn.execute("SELECT file_path FROM themes WHERE id = ?", (theme_id,)).fetchone()
                if row and row["file_path"] and os.path.isfile(row["file_path"]):
                    os.remove(row["file_path"])
                conn.execute("DELETE FROM themes WHERE id = ?", (theme_id,))
                conn.commit()
                _compact_ids(conn, "themes")
                conn.commit()
            finally:
                conn.close()
            return jsonify({"code": 200, "message": "主题已删除"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/sites/<int:site_id>/install-theme", methods=["POST"])
    @jwt_required()
    def install_theme_to_site(site_id):
        """Install and activate a theme on a WordPress site."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            theme_ids = data.get("theme_ids", [])
            if not theme_ids:
                return jsonify({"code": 400, "message": "未选择主题"}), 400

            site_url = site["url"].rstrip("/")
            admin_user = site.get("admin_name", "admin")
            admin_password = site.get("admin_password", "")

            results = install_themes_to_site(site_url, admin_user, admin_password, theme_ids)
            return jsonify({"code": 200, "data": {"results": results}})
        except Exception as e:
            logger.error(f"Failed to install theme: {e}")
            return jsonify({"code": 500, "message": f"安装主题失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/install-plugins", methods=["POST"])
    @jwt_required()
    def install_plugins_to_site_endpoint(site_id):
        """Install and activate plugins on an existing WordPress site."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            plugin_ids = data.get("plugin_ids", [])
            if not plugin_ids:
                return jsonify({"code": 400, "message": "未选择插件"}), 400

            site_url = site["url"].rstrip("/")
            admin_user = site.get("admin_name", "admin")
            admin_password = site.get("admin_password", "")

            results = install_plugins_to_site(site_url, admin_user, admin_password, plugin_ids)
            return jsonify({"code": 200, "data": {"results": results}})
        except Exception as e:
            logger.error(f"Failed to install plugins: {e}")
            return jsonify({"code": 500, "message": f"安装插件失败: {str(e)[:100]}"}), 500

    # ---- Cloudflare ----
    def _get_cf_client(account_id=None):
        """Create a CloudflareClient from stored credentials (supports multi-account)."""
        from cloudflare_client import CloudflareClient
        creds = _get_cf_credentials(account_id)
        return CloudflareClient(
            api_token=creds.get("api_token"),
            api_email=creds.get("api_email"),
            api_key=creds.get("api_key"),
        )

    def _has_cf_credentials():
        """Check if any Cloudflare credentials are stored."""
        # Check accounts table first
        accts = list_cf_accounts(hide_secrets=True)
        if accts:
            return True
        # Fallback to old format
        creds = _get_cf_credentials()
        return bool(creds.get("api_token") or (creds.get("api_email") and creds.get("api_key")))

    # ---- Cloudflare Account Management ----

    @app.route("/api/cloudflare/accounts", methods=["GET"])
    @jwt_required()
    def cf_list_accounts():
        """List all saved Cloudflare accounts (credentials masked)."""
        try:
            accounts = list_cf_accounts(hide_secrets=True)
            return jsonify({"code": 200, "data": accounts})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts", methods=["POST"])
    @jwt_required()
    def cf_create_account():
        """Add a new Cloudflare account after verifying credentials."""
        try:
            data = request.get_json(silent=True) or {}
            api_token = (data.get("api_token") or "").strip()
            api_email = (data.get("api_email") or "").strip()
            api_key = (data.get("api_key") or "").strip()
            name = (data.get("name") or "").strip()

            if not api_token and not (api_email and api_key):
                return jsonify({"code": 400, "message": "请提供API Token 或 邮箱+Global API Key"}), 400

            from cloudflare_client import CloudflareClient
            cf = CloudflareClient(api_token=api_token or None, api_email=api_email or None, api_key=api_key or None)
            resp = cf.verify_token()

            if not resp.get("success"):
                errors = resp.get("errors", [])
                err_msg = str(errors)
                if any("6003" in str(e.get("code", "")) or "6111" in str(e.get("code", "")) for e in errors):
                    err_msg = "认证格式无效"
                elif any("1000" in str(e.get("code", "")) for e in errors):
                    err_msg = "API Token无效"
                return jsonify({"code": 400, "message": f"验证失败: {err_msg}"}), 400

            # Auto-generate name if empty
            if not name:
                if api_token:
                    name = f"账号-{api_token[:6]}"
                else:
                    name = api_email

            auth_type = "token" if api_token else "global"
            acct = create_cf_account({
                "name": name,
                "api_token": api_token,
                "api_email": api_email,
                "api_key": api_key,
                "auth_type": auth_type,
            })

            return jsonify({"code": 200, "data": acct, "message": "账号已保存"})
        except Exception as e:
            logger.error(f"CF create account failed: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts/<int:account_id>", methods=["DELETE"])
    @jwt_required()
    def cf_delete_account(account_id):
        """Delete a Cloudflare account."""
        try:
            delete_cf_account(account_id)
            return jsonify({"code": 200, "message": "账号已删除"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts/<int:account_id>/default", methods=["PUT"])
    @jwt_required()
    def cf_set_default_account(account_id):
        """Set a Cloudflare account as the default."""
        try:
            set_default_cf_account(account_id)
            return jsonify({"code": 200, "message": "已设为默认账号"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/verify", methods=["POST"])
    @jwt_required()
    def cf_verify_token():
        """Verify Cloudflare credentials and save as account."""
        try:
            data = request.get_json(silent=True) or {}
            api_token = (data.get("api_token") or "").strip()
            api_email = (data.get("api_email") or "").strip()
            api_key = (data.get("api_key") or "").strip()

            if not api_token and not (api_email and api_key):
                return jsonify({"code": 400, "message": "请提供API Token 或 邮箱+Global API Key"}), 400

            from cloudflare_client import CloudflareClient
            cf = CloudflareClient(api_token=api_token or None, api_email=api_email or None, api_key=api_key or None)
            resp = cf.verify_token()

            if resp.get("success"):
                # Save to old global_config for backward compatibility
                _set_config_value("cf_api_token", api_token)
                _set_config_value("cf_api_email", api_email)
                _set_config_value("cf_api_key", api_key)

                # Also save as account (deduplicate by token/email)
                auth_type = "token" if api_token else "global"
                name = data.get("name", "").strip()
                if not name:
                    name = f"账号-{api_token[:6]}" if api_token else api_email
                create_cf_account({
                    "name": name,
                    "api_token": api_token,
                    "api_email": api_email,
                    "api_key": api_key,
                    "auth_type": auth_type,
                })
                return jsonify({"code": 200, "data": resp.get("result", {})})

            # Parse error for user-friendly message
            errors = resp.get("errors", [])
            err_msg = str(errors)
            if any("6003" in str(e.get("code", "")) or "6111" in str(e.get("code", "")) for e in errors):
                err_msg = "认证格式无效，请检查：1) API Token格式是否正确（不应含空格或特殊字符）；2) 如使用Global API Key，请同时填写邮箱和Key"
            elif any("1000" in str(e.get("code", "")) for e in errors):
                err_msg = "API Token无效，请确认Token是否正确且未过期"
            return jsonify({"code": 400, "message": f"验证失败: {err_msg}"}), 400
        except Exception as e:
            logger.error(f"CF verify failed: {e}")
            return jsonify({"code": 500, "message": f"验证失败: {str(e)[:100]}"}), 500

    @app.route("/api/cloudflare/zones", methods=["GET"])
    @jwt_required()
    def cf_list_zones():
        """List Cloudflare zones. Query: ?account_id=<id> to use specific account."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            resp = cf.list_zones()
            if resp.get("success"):
                return jsonify({"code": 200, "data": resp.get("result", [])})
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/dns-records/<zone_id>", methods=["GET"])
    @jwt_required()
    def cf_list_dns(zone_id):
        """List DNS records for a zone. Query: ?account_id=<id> to use specific account."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            resp = cf.list_dns_records(zone_id)
            if resp.get("success"):
                return jsonify({"code": 200, "data": resp.get("result", [])})
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/sites/<int:site_id>/dns", methods=["POST"])
    @jwt_required()
    def cf_create_dns(site_id):
        """Create a DNS A record for a site via Cloudflare. Body: zone_id, proxied, server_ip, account_id."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            zone_id = data.get("zone_id")
            proxied = data.get("proxied", False)
            account_id = data.get("account_id")

            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400

            server_ip = data.get("server_ip") or _get_config_value("panel_server_ip") or config.PANEL_HOST

            cf = _get_cf_client(account_id)

            domain = site["site_name"]
            if not zone_id:
                zone = cf.find_zone_by_name(domain)
                if not zone:
                    return jsonify({"code": 404, "message": f"未在Cloudflare中找到域名 {domain} 的区域，请确认域名已添加到Cloudflare"}), 404
                zone_id = zone["id"]

            resp = cf.create_dns_record(zone_id, "A", domain, server_ip, proxied=proxied)
            if resp.get("success"):
                record = resp.get("result", {})
                update_site_fields(site_id, {
                    "cf_zone_id": zone_id,
                    "cf_dns_record_id": record.get("id", ""),
                })
                return jsonify({"code": 200, "data": record})
            return jsonify({"code": 500, "message": "DNS记录创建失败: " + str(resp.get("errors", []))}), 500
        except Exception as e:
            logger.error(f"CF DNS create failed: {e}")
            return jsonify({"code": 500, "message": f"DNS创建失败: {str(e)[:100]}"}), 500

    @app.route("/api/cloudflare/status", methods=["GET"])
    @jwt_required()
    def cf_status():
        """Check Cloudflare connection status. Query: ?account_id=<id>."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 200, "data": {"connected": False}})
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            resp = cf.verify_token()
            return jsonify({"code": 200, "data": {"connected": resp.get("success", False)}})
        except Exception:
            return jsonify({"code": 200, "data": {"connected": False}})

