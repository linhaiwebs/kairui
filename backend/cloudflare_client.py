"""Cloudflare API client for DNS management."""
import logging
import requests

logger = logging.getLogger(__name__)


class CloudflareClient:
    """Minimal Cloudflare API v4 client for DNS A record management.

    Supports two authentication methods:
    1. API Token: Bearer token (recommended)
    2. Global API Key: email + key (legacy)
    """

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token=None, api_email=None, api_key=None):
        self.api_token = api_token.strip() if api_token else None
        self.api_email = api_email.strip() if api_email else None
        self.api_key = api_key.strip() if api_key else None

    def _headers(self):
        if self.api_token:
            return {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            }
        elif self.api_email and self.api_key:
            return {
                "X-Auth-Email": self.api_email,
                "X-Auth-Key": self.api_key,
                "Content-Type": "application/json",
            }
        return {"Content-Type": "application/json"}

    def _request(self, method, path, json_data=None):
        url = f"{self.BASE_URL}{path}"
        try:
            resp = requests.request(method, url, headers=self._headers(), json=json_data, timeout=30)
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Cloudflare API error: {e}")
            return {"success": False, "errors": [{"message": str(e)}]}

    def verify_token(self):
        """Verify the API token or Global API Key is valid.

        Uses /zones endpoint for verification because:
        - /user/tokens/verify requires User:Read permission that many scoped tokens lack
        - /zones only needs Zone:Read, which is typically granted
        """
        if self.api_token:
            resp = self._request("GET", "/zones?per_page=1")
            if resp.get("success"):
                return {"success": True, "result": {"status": "active", "type": "api_token"}}
            # If token lacks zone access, try user-level verification as fallback
            if resp.get("errors") and any(
                "authentication" in str(e.get("message", "")).lower()
                for e in resp.get("errors", [])
            ):
                user_resp = self._request("GET", "/user")
                if user_resp.get("success"):
                    return {"success": True, "result": {"status": "active", "type": "api_token"}}
                return user_resp
            return resp
        elif self.api_email and self.api_key:
            resp = self._request("GET", "/zones?per_page=1")
            if resp.get("success"):
                return {"success": True, "result": {"status": "active", "type": "global_api_key"}}
            return resp
        return {"success": False, "errors": [{"message": "No credentials provided"}]}

    def list_zones(self, page=1, per_page=50):
        """List all zones (domains) the token has access to."""
        return self._request("GET", f"/zones?page={page}&per_page={per_page}")

    def get_zone(self, zone_id):
        """Get details of a specific zone."""
        return self._request("GET", f"/zones/{zone_id}")

    def find_zone_by_name(self, domain):
        """Find a zone by domain name (matches root domain from subdomain)."""
        # Extract root domain: a.b.example.com -> example.com
        parts = domain.rstrip(".").split(".")
        for i in range(len(parts)):
            candidate = ".".join(parts[i:])
            resp = self._request("GET", f"/zones?name={candidate}")
            if resp.get("success") and resp.get("result"):
                return resp["result"][0]
        return None

    def list_dns_records(self, zone_id, record_type=None, name=None):
        """List DNS records for a zone."""
        params = []
        if record_type:
            params.append(f"type={record_type}")
        if name:
            params.append(f"name={name}")
        qs = "&".join(params)
        path = f"/zones/{zone_id}/dns_records"
        if qs:
            path += f"?{qs}"
        return self._request("GET", path)

    def create_dns_record(self, zone_id, record_type, name, content, proxied=False, ttl=1):
        """Create a DNS record. ttl=1 means auto (Cloudflare default)."""
        return self._request("POST", f"/zones/{zone_id}/dns_records", {
            "type": record_type,
            "name": name,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        })

    def delete_dns_record(self, zone_id, record_id):
        """Delete a DNS record."""
        return self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")


# Singleton instance
cf_client = CloudflareClient()
