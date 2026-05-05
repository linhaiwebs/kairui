import functools
import json
import logging
import os
import subprocess
import time
import uuid

from flask import jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, verify_jwt_in_request

from config import config
from models import (
    create_plugin,
    create_site,
    delete_plugin,
    delete_site,
    get_enabled_plugins,
    get_global_config,
    get_plugin,
    get_site,
    init_db,
    list_plugins,
    list_sites,
    update_global_config,
    update_site,
)
from panel_client import panel_client

logger = logging.getLogger(__name__)

# Directory to store uploaded plugin files
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)


def install_plugins_to_site(container_alias, plugin_ids):
    """Install plugins to a WordPress site running in a Docker container.
    
    Uses docker cp to copy the plugin zip, then docker exec to unzip and activate.
    The container name in 1Panel follows the pattern: {appKey}-{alias} or just alias.
    """
    results = []
    for pid in plugin_ids:
        plugin = get_plugin(pid)
        if not plugin or not plugin.get("file_path") or not os.path.isfile(plugin["file_path"]):
            results.append({"plugin": pid, "status": "error", "message": "插件文件不存在"})
            continue

        plugin_filename = plugin["filename"]
        plugin_name = plugin["name"]

        # Try different container name patterns used by 1Panel
        container_names = [
            container_alias,
            f"wordpress-{container_alias}",
            f"{container_alias}-wordpress",
        ]

        # Find the actual container
        container_id = None
        for cname in container_names:
            try:
                check = subprocess.run(
                    ["docker", "inspect", "--format", "{{.Id}}", cname],
                    capture_output=True, text=True, timeout=5
                )
                if check.returncode == 0 and check.stdout.strip():
                    container_id = cname
                    break
            except Exception:
                continue

        # If not found by name, try partial match
        if not container_id:
            try:
                ps = subprocess.run(
                    ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={container_alias}"],
                    capture_output=True, text=True, timeout=5
                )
                if ps.returncode == 0 and ps.stdout.strip():
                    container_id = ps.stdout.strip().split("\n")[0]
            except Exception:
                pass

        if not container_id:
            results.append({
                "plugin": plugin_name,
                "status": "error",
                "message": f"未找到容器 {container_alias}，插件需手动安装"
            })
            continue

        try:
            # Copy plugin zip to container
            dest = f"/tmp/{plugin_filename}"
            cp = subprocess.run(
                ["docker", "cp", plugin["file_path"], f"{container_id}:{dest}"],
                capture_output=True, text=True, timeout=30
            )
            if cp.returncode != 0:
                results.append({
                    "plugin": plugin_name, "status": "error",
                    "message": f"复制插件失败: {cp.stderr[:100]}"
                })
                continue

            # Unzip to wp-content/plugins
            unzip = subprocess.run(
                ["docker", "exec", container_id,
                 "unzip", "-o", dest, "-d", "/var/www/html/wp-content/plugins/"],
                capture_output=True, text=True, timeout=30
            )

            # Clean up temp file
            subprocess.run(
                ["docker", "exec", container_id, "rm", "-f", dest],
                capture_output=True, text=True, timeout=10
            )

            if unzip.returncode != 0:
                results.append({
                    "plugin": plugin_name, "status": "error",
                    "message": f"解压插件失败: {unzip.stderr[:100]}"
                })
                continue

            # Try to activate via WP-CLI if available
            # Determine the plugin directory name from zip
            plugin_dir_name = plugin_name
            activate = subprocess.run(
                ["docker", "exec", container_id,
                 "wp", "plugin", "activate", plugin_dir_name,
                 "--allow-root", "--path=/var/www/html"],
                capture_output=True, text=True, timeout=30
            )

            if activate.returncode == 0:
                results.append({
                    "plugin": plugin_name, "status": "success",
                    "message": "插件已安装并启用"
                })
            else:
                # WP-CLI might not be available, plugin is still installed (just not activated)
                results.append({
                    "plugin": plugin_name, "status": "success",
                    "message": "插件已安装，需手动启用（WP-CLI不可用）"
                })

        except subprocess.TimeoutExpired:
            results.append({
                "plugin": plugin_name, "status": "error",
                "message": "操作超时"
            })
        except Exception as e:
            results.append({
                "plugin": plugin_name, "status": "error",
                "message": str(e)[:100]
            })

    return results


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
            delete_site(site_id)
            return jsonify({"code": 200, "message": "Deleted"})
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
        """Create multiple WordPress sites in batch via 1Panel."""
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

            # Get database service info
            db_service = data.get("db_service") or global_cfg.get("db_service", "mariadb")
            db_services_resp = panel_client.get_app_services(db_service)
            if db_services_resp.get("code") != 200:
                return jsonify({"code": 500, "message": f"获取数据库服务信息失败: {db_services_resp.get('message', '')}"}), 500

            db_services_data = db_services_resp.get("data", [])
            db_service_info = db_services_data[0] if db_services_data else None
            if not db_service_info:
                return jsonify({"code": 500, "message": "没有可用的数据库服务"}), 500

            results = []
            base_port = data.get("base_port", 8081)
            used_ports = set()

            # Get ALL currently used ports from 1Panel (not just WordPress)
            try:
                installed_resp = panel_client.search_installed_apps(page=1, page_size=200)
                if installed_resp.get("code") == 200:
                    for item in installed_resp.get("data", {}).get("items", []):
                        if item.get("httpPort"):
                            if item.get("httpsPort"):
                                used_ports.add(item["httpsPort"])
                            used_ports.add(item["httpPort"])
            except Exception:
                pass

            # Also scan host ports via ss/netstat for safety
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

                # Find available port - always scan from base_port upward
                port = find_available_port(base_port)

                # Generate DB credentials
                import string
                import random
                db_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
                db_name = f"wp_{db_suffix}"
                db_user = f"wp_{db_suffix}"
                db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=16))

                # Install WordPress app
                install_params = {
                    "PANEL_DB_TYPE": db_service,
                    "PANEL_DB_NAME": db_name,
                    "PANEL_DB_USER": db_user,
                    "PANEL_DB_USER_PASSWORD": db_pass,
                    "PANEL_APP_PORT_HTTP": port,
                }

                services = {db_service: db_service_info.get("value", db_service)}

                try:
                    install_resp = panel_client.install_app(
                        app_detail_id=app_detail_id,
                        name=alias,
                        params=install_params,
                        services=services,
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
                try:
                    new_installed = panel_client.search_installed_apps(name="wordpress")
                    if new_installed.get("code") == 200:
                        new_app = next((a for a in new_installed.get("data", {}).get("items", []) if a.get("name") == alias), None)
                        if new_app:
                            app_install_id = new_app.get("id")
                except Exception:
                    pass

                # Create website
                try:
                    website_resp = panel_client.create_website(
                        primary_domain=domain,
                        alias=alias,
                        app_type="installed",
                        app_install_id=app_install_id,
                        website_group_id=data.get("website_group_id", 1),
                        proxy=f"http://127.0.0.1:{port}",
                    )
                except Exception as e:
                    logger.warning(f"Failed to create website for {domain}: {e}")
                    website_resp = {"code": 500}

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

                # Install plugins if any
                plugin_results = []
                if plugin_ids and app_install_id:
                    time.sleep(5)  # Wait for WordPress to fully start
                    try:
                        plugin_results = install_plugins_to_site(alias, plugin_ids)
                    except Exception as e:
                        logger.warning(f"Plugin installation failed for {domain}: {e}")
                        plugin_results = [{"plugin": pid, "status": "error", "message": str(e)[:80]} for pid in plugin_ids]

                results.append({
                    "domain": domain,
                    "status": "success",
                    "port": port,
                    "site_id": site["id"] if site else None,
                    "panel_website_id": panel_website_id,
                    "panel_app_install_id": app_install_id,
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
