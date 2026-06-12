"""WordPress.com REST API client for site creation and domain mapping."""

import requests as http_requests


class WordPressComClient:
    BASE_URL = "https://public-api.wordpress.com/rest/v1.1"
    OAUTH_BASE = "https://public-api.wordpress.com/oauth2"

    def __init__(self, access_token=None):
        self.access_token = access_token

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method, path, json_data=None):
        url = f"{self.BASE_URL}{path}"
        kwargs = {"headers": self._headers(), "timeout": 30}
        if json_data:
            kwargs["json"] = json_data
        resp = http_requests.request(method, url, **kwargs)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text, "status_code": resp.status_code}

    @classmethod
    def get_auth_url(cls, client_id, redirect_uri):
        """Build WordPress.com OAuth2 authorization URL."""
        return (
            f"{cls.OAUTH_BASE}/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope=global"
        )

    @classmethod
    def exchange_code(cls, client_id, client_secret, code, redirect_uri):
        """Exchange authorization code for an access token."""
        resp = http_requests.post(
            "https://public-api.wordpress.com/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        return resp.json()

    def get_me(self):
        """Get current user info (validates token)."""
        return self._request("GET", "/me")

    def get_primary_site(self):
        """Get the user's primary WordPress.com site."""
        resp = self._request("GET", "/me/sites")
        if resp.get("sites"):
            return resp["sites"][0]
        return None

    def map_domain(self, site_id, domain):
        """Register/map a custom domain to a WordPress.com site.

        POST /sites/{site_id}/domains/new
        Requires a paid plan for domain mapping via API.
        """
        return self._request("POST", f"/sites/{site_id}/domains/new", {
            "domain": domain,
        })

    def check_domain_mapping(self, site_id, domain):
        """Check the status of a domain mapping."""
        resp = self._request("GET", f"/sites/{site_id}/domains/{domain}")
        return resp
