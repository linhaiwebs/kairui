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
        # If host/port/api_key were explicitly passed (non-None), use them directly
        if self.host is not None and self.port is not None and self.api_key is not None:
            return (self.host, self.port, self.api_key)
        # Fallback to Flask config
        try:
            cfg = current_app.config
        except RuntimeError:
            # No Flask app context (background thread) — fallback to defaults
            return (
                self.host or "127.0.0.1",
                self.port or 3500,
                self.api_key or "",
            )
        return (
            self.host or cfg.get("PANEL_HOST", "127.0.0.1"),
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
        url = f"{self._base_url()}/api/v1{path}"
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

    # ---- PHP Runtime Config ----
    def update_php_config(self, install_id, max_execution_time=300, memory_limit="256M",
                          upload_max_filesize="64M", max_input_vars=3000):
        """Update PHP runtime settings for an installed app via 1Panel API."""
        params = {
            "maxExecutionTime": max_execution_time,
            "memoryLimit": memory_limit,
            "uploadMaxFilesize": upload_max_filesize,
            "maxInputVars": max_input_vars,
        }
        return self._request(
            "POST",
            f"/apps/installed/params/update",
            {
                "installId": install_id,
                "advanced": True,
                "params": params,
            },
        )

    # ---- Databases ----
    def search_databases(self, page=1, page_size=100, name="", db_type="mariadb"):
        return self._request(
            "POST",
            "/databases/search",
            {
                "Page": page, "PageSize": page_size, "Name": name,
                "Type": db_type, "Database": db_type,
                "OrderBy": "name", "Order": "ascending",
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

    def delete_database(self, database_id, db_type="mariadb", delete_user=False, force_delete=False):
        return self._request(
            "POST",
            "/databases/del",
            {
                "id": database_id,
                "Type": db_type,
                "Database": db_type,
                "DeleteUser": delete_user,
                "ForceDelete": force_delete,
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
    def search_websites(self, page=1, page_size=100, name="", order_by="favorite", order="descending",
                        website_group_id=0, website_type=""):
        body = {
            "name": name,
            "page": page,
            "pageSize": page_size,
            "orderBy": order_by,
            "order": order,
            "websiteGroupId": website_group_id,
            "type": website_type,
        }
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
        app_install_params=None,
        services=None,
        website_group_id=1,
        remark="",
        enable_ipv6=False,
        proxy="",
        port=9000,
        runtime_type="php",
        domains=None,
    ):
        """Create a deployment website in 1Panel (一键部署).

        Request body matches 1Panel UI's actual fetch request exactly.

        For app_type="new": installs the app AND creates the website in ONE step.
        For app_type="installed": links an existing installed app to a domain.

        Args:
            primary_domain: Main domain (e.g. "example.com")
            alias: Site alias (e.g. "example.com"), must be unique across 1Panel
            app_type: "new" (install app + create website) or "installed" (link existing app)
            app_install_id: ID of the installed app (required for app_type="installed")
            app_detail_id: App detail ID (required for app_type="new")
            app_id: App ID (for app_type="new")
            app_install_params: Dict of install params for app_type="new"
            services: Dict of service dependencies for app_type="new"
            website_group_id: Website group ID (default: 1)
            remark: Site remark/description
            enable_ipv6: Enable IPv6 listening
            proxy: Proxy URL (optional)
            port: Container port (default: 9000, as in 1Panel UI)
            runtime_type: Runtime type (default: "php")
            domains: List of domain dicts [{"domain":"...","host":"...","port":80,"ssl":false}]
        """
        import uuid

        # Build domains list (1Panel UI always includes this)
        domains_list = domains or []
        if not domains_list and alias:
            domains_list = [{
                "domain": alias,
                "host": alias,
                "port": 80,
                "ssl": False,
            }]

        # Build appinstall sub-object (1Panel UI always sends this, even for installed type)
        app_install_body = {
            "appId": app_id or 0,
            "name": "",
            "appDetailId": 0,
            "params": {},
            "version": "",
            "appkey": "",
            "advanced": False,
            "cpuQuota": 0,
            "memoryLimit": 0,
            "memoryUnit": "MB",
            "containerName": "",
            "allowPort": False,
            "format": "utf8mb4",
            "collation": "",
        }

        if app_type == "new":
            app_install_body["name"] = alias
            app_install_body["appDetailId"] = app_detail_id or 0
            app_install_body["params"] = app_install_params or {}
            app_install_body["advanced"] = True
            app_install_body["allowPort"] = True
            if app_id:
                app_install_body["appId"] = app_id

        # Build the complete request body matching 1Panel UI format exactly
        body = {
            "primaryDomain": primary_domain,
            "type": "deployment",
            "alias": alias,
            "remark": remark,
            "appType": app_type,
            "webSiteGroupId": website_group_id,
            "otherDomains": "",
            "proxy": proxy or "",
            "appinstall": app_install_body,
            "IPV6": enable_ipv6,
            "enableFtp": False,
            "ftpUser": "",
            "ftpPassword": "",
            "proxyType": "tcp",
            "port": port,
            "proxyProtocol": "http://",
            "proxyAddress": "",
            "runtimeType": runtime_type,
            "taskID": str(uuid.uuid4()),
            "createDb": False,
            "dbName": "",
            "dbPassword": "",
            "dbFormat": "utf8mb4",
            "dbUser": "",
            "dbType": "mysql",
            "dbHost": "",
            "enableSSL": False,
            "domains": domains_list,
            "siteDir": "",
            "streamPorts": "",
            "udp": False,
            "name": "",
            "algorithm": "",
            "servers": [],
        }

        if app_type == "installed" and app_install_id:
            body["appInstallId"] = app_install_id

        # For "new" type, pass services via params if provided
        if app_type == "new" and services:
            body["appinstall"]["params"]["services"] = services

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
                "DeleteApp": delete_app,
                "DeleteBackup": delete_backup,
                "ForceDelete": force_delete,
                "DeleteDB": delete_db,
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

    def create_static_website(self, domain, alias, website_group_id=1):
        """Create a static website in 1Panel (no app deployment, just directory + nginx).

        Returns siteDir path on success (e.g. /opt/1panel/apps/openresty/openresty/www/sites/{alias}/index).
        """
        import uuid, time as _time
        task_id = str(uuid.uuid4())
        body = {
            "primaryDomain": domain,
            "type": "static",
            "alias": alias,
            "appType": "new",
            "webSiteGroupId": website_group_id,
            "otherDomains": "",
            "proxy": "",
            "appinstall": {
                "appId": 0,
                "name": "",
                "appDetailId": 0,
                "params": {},
                "version": "",
                "appkey": "",
                "advanced": False,
                "cpuQuota": 0,
                "memoryLimit": 0,
                "memoryUnit": "MB",
                "containerName": "",
                "allowPort": False,
                "format": "utf8mb4",
                "collation": "",
            },
            "IPV6": False,
            "enableFtp": False,
            "ftpUser": "",
            "ftpPassword": "",
            "proxyType": "tcp",
            "port": 80,
            "proxyProtocol": "",
            "proxyAddress": "",
            "runtimeType": "",
            "taskID": task_id,
            "createDb": False,
            "dbName": "",
            "dbPassword": "",
            "dbFormat": "utf8mb4",
            "dbUser": "",
            "dbType": "mysql",
            "dbHost": "",
            "enableSSL": False,
            "domains": [{"domain": domain, "host": domain, "port": 80, "ssl": False}],
            "siteDir": "",
            "streamPorts": "",
            "udp": False,
            "name": "",
            "algorithm": "",
            "servers": [],
        }
        resp = self._request("POST", "/websites", body)
        if resp.get("code") == 200:
            # 1Panel creates site directory at .../sites/{alias}/index
            site_dir = f"/opt/1panel/apps/openresty/openresty/www/sites/{alias}/index"
            resp["data"] = {"website_id": None, "site_dir": site_dir, "alias": alias}

            # Search by domain (primaryDomain) to get actual website_id and siteDir
            for attempt in range(3):
                _time.sleep(1 if attempt == 0 else 2)
                ws = self.search_websites(name=domain)
                if ws.get("code") == 200:
                    for w in (ws.get("data") or {}).get("items", []) or []:
                        if w.get("primaryDomain") == domain:
                            resp["data"]["website_id"] = w.get("id")
                            actual_dir = w.get("siteDir", "")
                            if actual_dir:
                                resp["data"]["site_dir"] = actual_dir
                            break
                if resp["data"].get("website_id"):
                    break
        return resp

    def reload_openresty(self):
        """Reload OpenResty to apply new nginx configurations."""
        or_resp = self.search_installed_apps(name="openresty")
        if not or_resp or or_resp.get("code") != 200:
            return or_resp or {"code": 502, "message": "无法连接1Panel", "data": None}
        items = (or_resp.get("data") or {}).get("items", [])
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

    client_max_body_size 128m;

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

    def create_static_site_config(self, alias, domain, website_dir="/opt/1panel/www"):
        """Create OpenResty configuration for a static HTML site.

        Unlike create_nginx_proxy_config (reverse proxy to a container), this
        configures OpenResty to serve static HTML files directly from the site
        directory. No PHP/MySQL/container needed.

        Args:
            alias: Site alias (used for directory names)
            domain: Domain name for the site
            website_dir: Base directory for website files (default: /opt/1panel/www)

        Returns:
            dict with code and message
        """
        errors = []

        # Step 1: Create site directory structure
        site_dir = f"{website_dir}/sites/{alias}"
        index_dir = f"{site_dir}/index"
        for subdir in ["log", "index", "ssl"]:
            resp = self.create_file(f"{site_dir}/{subdir}", is_dir=True)
            if resp.get("code") not in (200, 500):
                errors.append(f"创建目录 {site_dir}/{subdir} 失败: {resp.get('message', '')}")

        # Create products subdirectory
        resp = self.create_file(f"{index_dir}/products", is_dir=True)
        if resp.get("code") not in (200, 500):
            errors.append(f"创建产品目录失败: {resp.get('message', '')}")

        # Create assets subdirectory
        resp = self.create_file(f"{index_dir}/assets", is_dir=True)
        if resp.get("code") not in (200, 500):
            errors.append(f"创建资源目录失败: {resp.get('message', '')}")

        # Create empty log files
        for log_file in ["access.log", "error.log"]:
            file_path = f"{site_dir}/log/{log_file}"
            self.create_file(file_path, is_dir=False)
            self.save_file(file_path, "")

        # Step 2: Create OpenResty static site configuration
        nginx_conf = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domain};

    root /www/sites/{alias}/index;
    index index.html;

    client_max_body_size 128m;

    access_log /www/sites/{alias}/log/access.log main;
    error_log /www/sites/{alias}/log/error.log;

    # SPA fallback + clean URLs
    location / {{
        try_files $uri $uri.html $uri/ /index.html;
    }}

    # Google Shopping Feed
    location = /feed.xml {{
        add_header Content-Type "application/rss+xml";
        add_header Access-Control-Allow-Origin "*";
    }}

    # Robots.txt
    location = /robots.txt {{
        add_header Content-Type "text/plain";
    }}

    # Static assets with long cache
    location /assets/ {{
        expires 30d;
        add_header Cache-Control "public, immutable";
    }}

    # Product images cache
    location /products/ {{
        expires 7d;
    }}
}}
"""
        conf_path = f"{website_dir}/conf.d/{alias}.conf"

        # Create and write the config file
        self.create_file(conf_path, is_dir=False)
        save_resp = self.save_file(conf_path, nginx_conf)
        if save_resp.get("code") != 200:
            errors.append(f"保存OpenResty配置失败: {save_resp.get('message', '')}")

        # Step 3: Reload OpenResty
        reload_resp = self.reload_openresty()
        if reload_resp.get("code") != 200:
            errors.append(f"重载OpenResty失败: {reload_resp.get('message', '')}")

        if errors:
            return {"code": 207, "message": "; ".join(errors), "data": {"conf_path": conf_path}}
        return {"code": 200, "message": "静态站点OpenResty配置创建成功", "data": {
            "conf_path": conf_path,
            "site_dir": site_dir,
            "index_dir": index_dir,
        }}

    def upload_static_site_files(self, alias, files, website_dir="/opt/1panel/www"):
        """Upload static HTML/CSS/image files to the 1Panel site directory.

        Args:
            alias: Site alias (ignored if website_dir is a full path)
            files: dict of {relative_path: content} e.g. {"index.html": "<html>...", "assets/style.css": "..."}
            website_dir: Base directory. If it contains '/index', use as-is.
                         Otherwise defaults to /opt/1panel/www/sites/{alias}/index

        Returns:
            dict with code, message, and uploaded file list
        """
        errors = []
        uploaded = []

        # Determine index directory
        if "/index" in website_dir or "sites/" in website_dir:
            index_dir = website_dir.rstrip("/")
        else:
            index_dir = f"{website_dir}/sites/{alias}/index"

        for rel_path, content in files.items():
            full_path = f"{index_dir}/{rel_path}"
            # Ensure parent directory exists
            parent_dir = "/".join(full_path.split("/")[:-1])
            if parent_dir != index_dir:
                self.create_file(parent_dir, is_dir=True)

            # Create file
            self.create_file(full_path, is_dir=False)
            # Write content
            save_resp = self.save_file(full_path, content)
            if save_resp.get("code") != 200:
                errors.append(f"上传 {rel_path} 失败: {save_resp.get('message', '')}")
            else:
                uploaded.append(rel_path)

        if errors:
            return {"code": 207, "message": "; ".join(errors), "data": {"uploaded": uploaded}}
        return {"code": 200, "message": f"成功上传 {len(uploaded)} 个文件", "data": {"uploaded": uploaded}}

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

    # ---- Image Management ----
    def search_images(self, page=1, page_size=100, name=""):
        """Search Docker images in 1Panel."""
        body = {"page": page, "pageSize": page_size}
        if name:
            body["name"] = name
        return self._request("POST", "/images/search", body)

    def clean_images(self, prune_all: bool = True):
        """Clean up unused Docker images.

        If prune_all=True, removes ALL unused images (docker image prune -a).
        If prune_all=False, only removes dangling images (docker image prune).
        """
        body = {"prune": True}
        if prune_all:
            body["pruneAll"] = True
        return self._request("POST", "/images/clean", body)

    def delete_images(self, image_ids):
        """Delete specific Docker images by their IDs."""
        return self._request("POST", "/images/del", {"ids": image_ids})


# Singleton instance
panel_client = OnePanelClient()
