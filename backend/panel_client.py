import base64
import hashlib
import time

import requests
from flask import current_app


class OnePanelClient:
    """Client for interacting with 1Panel v2 API."""

    def __init__(self, host=None, port=None, api_key=None):
        self.host = host
        self.port = port
        self.api_key = api_key

    def _get_config(self):
        cfg = current_app.config
        return (
            self.host or cfg.get("PANEL_HOST", "167.172.142.95"),
            self.port or cfg.get("PANEL_PORT", 3500),
            self.api_key or cfg.get("PANEL_API_KEY", ""),
        )

    def _base_url(self):
        host, port, _ = self._get_config()
        return f"http://{host}:{port}"

    def _headers(self):
        _, _, api_key = self._get_config()
        ts = str(int(time.time()))
        token = hashlib.md5(("1panel" + api_key + ts).encode()).hexdigest()
        return {
            "1Panel-Token": token,
            "1Panel-Timestamp": ts,
            "Content-Type": "application/json",
        }

    def _request(self, method, path, json_data=None, params=None):
        url = f"{self._base_url()}/api/v2{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), json=json_data, params=params, timeout=30
            )
        except requests.exceptions.ConnectionError:
            return {"code": 503, "message": "无法连接到1Panel服务", "data": None}
        except requests.exceptions.Timeout:
            return {"code": 504, "message": "1Panel服务响应超时", "data": None}
        except requests.exceptions.RequestException as e:
            return {"code": 502, "message": f"1Panel请求失败: {str(e)[:100]}", "data": None}

        if resp.status_code == 404:
            return {"code": 404, "message": "API endpoint not found", "data": None}
        try:
            return resp.json()
        except Exception:
            return {"code": resp.status_code, "message": resp.text[:200], "data": None}

    # ---- Auth ----
    def get_captcha(self):
        return self._request("GET", "/auth/captcha")

    def login(self, name, password, auth_method="jwt", language="en"):
        return self._request(
            "POST",
            "/auth/login",
            {
                "name": name,
                "password": password,
                "authMethod": auth_method,
                "language": language,
            },
        )

    # ---- Apps ----
    def search_apps(self, name="", page=1, page_size=100):
        return self._request(
            "POST",
            "/apps/search",
            {"page": page, "pageSize": page_size, "name": name},
        )

    def get_app(self, key):
        return self._request("GET", f"/apps/{key}")

    def get_app_detail(self, app_id, version, detail_type="app"):
        return self._request("GET", f"/apps/detail/{app_id}/{version}/{detail_type}")

    def install_app(self, app_detail_id, name, params, services=None, advanced=True, allow_port=True, pull_image=True):
        body = {
            "appDetailId": app_detail_id,
            "name": name,
            "params": params,
            "advanced": advanced,
            "allowPort": allow_port,
            "pullImage": pull_image,
        }
        if services:
            body["services"] = services
        return self._request("POST", "/apps/install", body)

    def search_installed_apps(self, page=1, page_size=100, name="", app_type=""):
        body = {"page": page, "pageSize": page_size}
        if name:
            body["name"] = name
        if app_type:
            body["type"] = app_type
        return self._request("POST", "/apps/installed/search", body)

    def get_installed_list(self):
        return self._request("GET", "/apps/installed/list")

    def get_installed_params(self, install_id):
        return self._request("GET", f"/apps/installed/params/{install_id}")

    def check_app_installed(self, key, name=""):
        return self._request("POST", "/apps/installed/check", {"key": key, "name": name})

    def operate_installed(self, install_id, operate, force_delete=False, delete_backup=True, delete_db=True):
        return self._request(
            "POST",
            "/apps/installed/op",
            {
                "installId": install_id,
                "operate": operate,
                "forceDelete": force_delete,
                "deleteBackup": delete_backup,
                "deleteDB": delete_db,
            },
        )

    def get_app_services(self, key):
        return self._request("GET", f"/apps/services/{key}")

    # ---- Databases ----
    def search_databases(self, page=1, page_size=100, name="", db_type="mariadb"):
        return self._request(
            "POST",
            "/databases/search",
            {
                "page": page, "pageSize": page_size, "name": name,
                "type": db_type, "database": db_type,
                "orderBy": "name", "order": "ascending",
            },
        )

    def create_database(self, name, db_type="mariadb", username="", password="",
                        permission="%", format_str="utf8mb4", from_source="local"):
        """Create a database in 1Panel's managed MariaDB/MySQL.
        
        The password must be base64-encoded per 1Panel API requirements.
        The 'database' field is the 1Panel database service name (e.g., 'mariadb').
        """
        encoded_password = base64.b64encode(password.encode()).decode()
        return self._request(
            "POST",
            "/databases",
            {
                "name": name,
                "type": db_type,
                "username": username,
                "password": encoded_password,
                "permission": permission,
                "database": db_type,
                "format": format_str,
                "from": from_source,
            },
        )

    def delete_database(self, database_id, delete_user=False, force_delete=False):
        return self._request(
            "POST",
            "/databases/del",
            {
                "id": database_id,
                "deleteUser": delete_user,
                "forceDelete": force_delete,
            },
        )

    def update_installed(self, install_id, params=None, advanced=True, allow_port=True,
                         pull_image=False, edit_compose=False, docker_compose=""):
        """Update an installed app's parameters via 1Panel API.
        
        This writes the params to the .env file and rebuilds the container.
        Key use case: fix allowPort for external access after installation.
        """
        body = {
            "installId": install_id,
            "advanced": advanced,
            "allowPort": allow_port,
            "pullImage": pull_image,
            "editCompose": edit_compose,
        }
        if params:
            body["params"] = params
        if docker_compose:
            body["dockerCompose"] = docker_compose
        return self._request("POST", "/apps/installed/params/update", body)

    # ---- Websites ----
    def search_websites(self, page=1, page_size=100, name="", order_by="created_at", order="descending"):
        body = {
            "page": page,
            "pageSize": page_size,
            "orderBy": order_by,
            "order": order,
        }
        if name:
            body["name"] = name
        return self._request("POST", "/websites/search", body)

    def get_websites_list(self):
        return self._request("GET", "/websites/list")

    def create_website(
        self,
        primary_domain,
        alias,
        app_type="installed",
        app_install_id=None,
        app_detail_id=None,
        app_id=None,
        website_group_id=1,
        remark="",
        enable_ipv6=True,
        proxy="",
    ):
        """Create a deployment website in 1Panel (一键部署).
        
        For app_type="installed", links an existing installed app (e.g. WordPress) to a domain.
        1Panel uses OpenResty to auto-generate reverse proxy config based on the app's port.
        
        Args:
            primary_domain: Main domain (e.g. "example.com")
            alias: Site alias, auto-filled from domain (e.g. "example-com"), must match installed app name
            app_type: "installed" (link existing app) or "new" (create new app)
            app_install_id: ID of the installed app (required for app_type="installed")
            app_detail_id: App detail ID (for app_type="new")
            app_id: App ID (for app_type="new")
            website_group_id: Website group ID (default: 1 = Default group)
            remark: Site remark/description
            enable_ipv6: Enable IPv6 listening (default: True)
            proxy: Proxy URL (optional)
        """
        domains = [{"domain": primary_domain, "port": 80, "ssl": False}]
        body = {
            "type": "deployment",
            "alias": alias,
            "webSiteGroupID": website_group_id,
            "IPV6": enable_ipv6,
            "domains": domains,
            "appType": app_type,
            "remark": remark,
        }
        if app_type == "installed" and app_install_id:
            body["appInstallID"] = app_install_id
        elif app_type == "new":
            body["appInstall"] = {
                "name": alias.replace(".", "-"),
                "appDetailID": app_detail_id,
                "params": {},
            }
            if app_id:
                body["appID"] = app_id
        if proxy:
            body["proxy"] = proxy
        return self._request("POST", "/websites", body)

    def check_website(self, primary_domain, app_type="installed", app_install_id=None, alias=""):
        body = {
            "primaryDomain": primary_domain,
            "type": "deployment",
            "alias": alias or primary_domain,
            "appType": app_type,
        }
        if app_install_id:
            body["appInstallID"] = app_install_id
        return self._request("POST", "/websites/check", body)

    def get_website(self, website_id):
        return self._request("GET", f"/websites/{website_id}")

    def delete_website(self, website_id, delete_app=True, delete_backup=True, force_delete=False, delete_db=True):
        return self._request(
            "POST",
            "/websites/del",
            {
                "id": website_id,
                "deleteApp": delete_app,
                "deleteBackup": delete_backup,
                "forceDelete": force_delete,
                "deleteDB": delete_db,
            },
        )

    def operate_website(self, website_id, operate):
        return self._request("POST", "/websites/operate", {"id": website_id, "operate": operate})

    def get_website_domains(self, website_id):
        return self._request("GET", f"/websites/domains/{website_id}")

    def create_website_domain(self, website_id, domains):
        return self._request(
            "POST",
            "/websites/domains",
            {"websiteID": website_id, "domains": domains},
        )

    # ---- Groups ----
    def search_groups(self, group_type="website"):
        return self._request("POST", "/groups/search", {
            "page": 1, "pageSize": 100,
            "type": group_type,
            "orderBy": "created_at", "order": "descending",
        })

    def create_group(self, name, group_type="website"):
        return self._request("POST", "/groups", {"name": name, "type": group_type})

    def ensure_website_group(self, group_name="Default", group_type="website"):
        """Ensure a website group exists and return its ID.
        
        Returns the ID of the default group, or creates one if none exist.
        Prefers groups with isDefault=True.
        """
        resp = self.search_groups(group_type)
        if resp.get("code") == 200:
            groups = resp.get("data", [])
            if isinstance(groups, list) and groups:
                # Prefer default group
                for g in groups:
                    if g.get("isDefault"):
                        return g.get("id", 1)
                # Fallback to first group
                return groups[0].get("id", 1)
        # Create the group
        create_resp = self.create_group(group_name, group_type)
        if create_resp.get("code") == 200:
            resp2 = self.search_groups(group_type)
            if resp2.get("code") == 200:
                groups = resp2.get("data", [])
                if isinstance(groups, list) and groups:
                    for g in groups:
                        if g.get("isDefault"):
                            return g.get("id", 1)
                    return groups[0].get("id", 1)
        return 1  # Fallback

    # ---- Website HTTPS ----
    def get_https_config(self, website_id):
        return self._request("GET", f"/websites/{website_id}/https")

    def update_https_config(self, website_id, enable=False, website_ssl_id=0,
                            http_config="HTTPToHTTPS", algorithm="", enable_hsts=False):
        body = {
            "websiteId": website_id,
            "enable": enable,
            "websiteSSLId": website_ssl_id,
            "httpConfig": http_config,
            "algorithm": algorithm,
            "hsts": enable_hsts,
        }
        return self._request("POST", f"/websites/{website_id}/https", body)

    # ---- Website Auth (HTTP Basic Auth) ----
    def get_auth_config(self, website_id):
        return self._request("POST", "/websites/auths", {"id": website_id})

    def update_auth_config(self, website_id, enable=False, username="", password=""):
        return self._request(
            "POST",
            "/websites/auths/update",
            {
                "id": website_id,
                "enable": enable,
                "username": username,
                "password": password,
            },
        )

    # ---- File Operations ----
    def create_file(self, path, is_dir=True):
        """Create a file or directory via 1Panel file API."""
        return self._request("POST", "/files", {"path": path, "isDir": is_dir})

    def save_file(self, path, content):
        """Save content to a file via 1Panel file API.
        Note: The file must already exist; create it with create_file first.
        """
        return self._request("POST", "/files/save", {"path": path, "content": content})

    def delete_file(self, path):
        """Delete a file or directory via 1Panel file API."""
        return self._request("POST", "/files/del", {"path": path})

    def read_file(self, path, page=1, page_size=5000):
        """Read file content via 1Panel file API."""
        return self._request("POST", "/files/read", {"path": path, "page": page, "pageSize": page_size})

    def reload_openresty(self):
        """Reload OpenResty to apply new nginx configurations."""
        # Find OpenResty install ID
        or_resp = self.search_installed_apps(name="openresty")
        if or_resp.get("code") != 200:
            return or_resp
        items = or_resp.get("data", {}).get("items", [])
        if not items:
            return {"code": 404, "message": "OpenResty未安装", "data": None}
        or_id = items[0]["id"]
        return self.operate_installed(or_id, "reload")

    def create_nginx_proxy_config(self, alias, domain, port, website_dir="/opt/1panel/www"):
        """Create nginx reverse proxy configuration for a WordPress site.
        
        This bypasses the broken POST /websites API by manually:
        1. Creating site directory structure
        2. Writing nginx proxy config to conf.d/
        3. Reloading OpenResty
        
        Args:
            alias: Site alias (used for directory names)
            domain: Domain name for the site
            port: HTTP port the WordPress container is listening on
            website_dir: Base directory for website files (default: /opt/1panel/www)
        
        Returns:
            dict with code and message
        """
        errors = []

        # Step 1: Create site directory structure
        site_dir = f"{website_dir}/sites/{alias}"
        for subdir in ["log", "index", "ssl", "proxy"]:
            resp = self.create_file(f"{site_dir}/{subdir}", is_dir=True)
            if resp.get("code") not in (200, 500):  # 500 = already exists, that's OK
                errors.append(f"创建目录 {site_dir}/{subdir} 失败: {resp.get('message', '')}")

        # Create empty log files
        for log_file in ["access.log", "error.log"]:
            file_path = f"{site_dir}/log/{log_file}"
            # Try creating the file
            self.create_file(file_path, is_dir=False)
            # Write empty content
            self.save_file(file_path, "")

        # Step 2: Create nginx proxy configuration
        nginx_conf = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    
    access_log /www/sites/{alias}/log/access.log main;
    error_log /www/sites/{alias}/log/error.log;
    
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        proxy_buffering off;
    }}
}}
"""
        conf_path = f"{website_dir}/conf.d/{alias}.conf"
        
        # Create and write the config file
        self.create_file(conf_path, is_dir=False)
        save_resp = self.save_file(conf_path, nginx_conf)
        if save_resp.get("code") != 200:
            errors.append(f"保存nginx配置失败: {save_resp.get('message', '')}")

        # Step 3: Reload OpenResty
        reload_resp = self.reload_openresty()
        if reload_resp.get("code") != 200:
            errors.append(f"重载OpenResty失败: {reload_resp.get('message', '')}")

        if errors:
            return {"code": 207, "message": "; ".join(errors), "data": {"conf_path": conf_path}}
        return {"code": 200, "message": "nginx配置创建成功", "data": {"conf_path": conf_path}}

    def delete_nginx_proxy_config(self, alias, domain="", website_dir="/opt/1panel/www"):
        """Delete nginx proxy configuration and site directory.
        
        Args:
            alias: Site alias used during creation
            domain: Domain name (unused but kept for API compatibility)
            website_dir: Base directory for website files
        """
        errors = []

        # Delete nginx config
        conf_path = f"{website_dir}/conf.d/{alias}.conf"
        del_conf = self.delete_file(conf_path)
        if del_conf.get("code") not in (200, 500):
            errors.append(f"删除nginx配置失败: {del_conf.get('message', '')}")

        # Delete site directory
        site_dir = f"{website_dir}/sites/{alias}"
        del_site = self.delete_file(site_dir)
        if del_site.get("code") not in (200, 500):
            errors.append(f"删除站点目录失败: {del_site.get('message', '')}")

        # Reload OpenResty
        reload_resp = self.reload_openresty()
        if reload_resp.get("code") != 200:
            errors.append(f"重载OpenResty失败: {reload_resp.get('message', '')}")

        if errors:
            return {"code": 207, "message": "; ".join(errors)}
        return {"code": 200, "message": "nginx配置已删除"}


# Singleton instance
panel_client = OnePanelClient()
