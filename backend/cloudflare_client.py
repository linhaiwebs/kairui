"""Cloudflare API client for DNS management."""
import logging
import requests

logger = logging.getLogger(__name__)


class CloudflareClient:
    """Minimal Cloudflare API v4 client for DNS A record management."""

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token=None):
        self.api_token = api_token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method, path, json_data=None):
        url = f"{self.BASE_URL}{path}"
        try:
            resp = requests.request(method, url, headers=self._headers(), json=json_data, timeout=30)
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Cloudflare API error: {e}")
            return {"success": False, "errors": [{"message": str(e)}]}

    def verify_token(self):
        """Verify the API token is valid."""
        return self._request("GET", "/user/tokens/verify")

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
