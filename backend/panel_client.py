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
        resp = requests.request(
            method, url, headers=self._headers(), json=json_data, params=params, timeout=30
        )
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

    def install_app(self, app_detail_id, name, params, services=None, advanced=False):
        body = {
            "appDetailId": app_detail_id,
            "name": name,
            "params": params,
            "advanced": advanced,
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
        other_domains="",
        proxy="",
    ):
        body = {
            "primaryDomain": primary_domain,
            "type": "deployment",
            "alias": alias,
            "webSiteGroupID": website_group_id,
            "appType": app_type,
            "remark": remark,
            "otherDomains": other_domains,
            "IPV6": False,
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

    def delete_website(self, website_id, delete_app=True, delete_backup=True, force_delete=False):
        return self._request(
            "POST",
            "/websites/del",
            {
                "id": website_id,
                "deleteApp": delete_app,
                "deleteBackup": delete_backup,
                "forceDelete": force_delete,
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
        return self._request("POST", "/groups/search", {"type": group_type})

    def create_group(self, name, group_type="website"):
        return self._request("POST", "/groups", {"name": name, "type": group_type})

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


# Singleton instance
panel_client = OnePanelClient()
