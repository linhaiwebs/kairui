"""Cloudflare API client for DNS management."""
import logging

import requests

logger = logging.getLogger(__name__)


class CloudflareClient:
    """Cloudflare API v4 client — API Token (Bearer) authentication."""

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token=None):
        self.api_token = api_token.strip() if api_token else None

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        } if self.api_token else {"Content-Type": "application/json"}

    def _request(self, method, path, json_data=None, extra_headers=None, data=None):
        url = f"{self.BASE_URL}{path}"
        hdrs = self._headers()
        if extra_headers:
            hdrs.update(extra_headers)
        try:
            if data is not None:
                resp = requests.request(method, url, headers=hdrs, data=data, timeout=30)
            else:
                resp = requests.request(method, url, headers=hdrs, json=json_data, timeout=30)
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"Cloudflare API error: {e}")
            return {"success": False, "errors": [{"message": str(e)}]}

    def verify_token(self):
        """Verify the API token — tries multiple endpoints to accommodate different token scopes."""
        if not self.api_token:
            return {"success": False, "errors": [{"message": "No API token provided"}]}
        # Try the dedicated token verification endpoint first
        resp = self._request("GET", "/user/tokens/verify")
        if resp.get("success") and resp.get("result", {}).get("status") == "active":
            return {"success": True, "result": {"status": "active"}}
        # Fallback: try /user (works for account-level tokens with user:read)
        user_resp = self._request("GET", "/user")
        if user_resp.get("success"):
            return {"success": True, "result": {"status": "active"}}
        # Last resort: try listing zones (works for zone-scoped tokens)
        zone_resp = self._request("GET", "/zones?per_page=1")
        if zone_resp.get("success"):
            return {"success": True, "result": {"status": "active"}}
        # All failed — return the most relevant error (prefer the first attempt)
        return resp

    def list_zones(self, page=1, per_page=50):
        return self._request("GET", f"/zones?page={page}&per_page={per_page}")

    def get_zone(self, zone_id):
        return self._request("GET", f"/zones/{zone_id}")

    def find_zone_by_name(self, domain):
        parts = domain.rstrip(".").split(".")
        for i in range(len(parts)):
            candidate = ".".join(parts[i:])
            resp = self._request("GET", f"/zones?name={candidate}")
            if resp.get("success") and resp.get("result"):
                return resp["result"][0]
        return None

    def add_zone(self, domain):
        """Add a new zone to Cloudflare. Returns the zone dict or None."""
        return self._add_zone_impl(domain)

    def create_zone(self, domain):
        """Alias for add_zone."""
        return self._add_zone_impl(domain)

    def _add_zone_impl(self, domain):
        # Try first without account ID (token may be scoped to one account)
        resp = self._request("POST", "/zones", {
            "name": domain,
            "jump_start": True,
        })
        if resp.get("success"):
            return resp.get("result")
        # If that failed due to missing account, try listing accounts and use the first one
        if resp.get("errors") and any(
            "account" in str(e.get("message", "")).lower()
            for e in resp.get("errors", [])
        ):
            accounts = self._request("GET", "/accounts")
            if accounts.get("success") and accounts.get("result"):
                account_id = accounts["result"][0]["id"]
                resp = self._request("POST", "/zones", {
                    "name": domain,
                    "jump_start": True,
                    "account": {"id": account_id},
                })
                if resp.get("success"):
                    return resp.get("result")
        logger.warning("add_zone failed for %s: %s", domain, resp.get("errors"))
        return None

    def list_dns_records(self, zone_id, record_type=None, name=None, page=1, per_page=10):
        params = []
        if record_type:
            params.append(f"type={record_type}")
        if name:
            params.append(f"name={name}")
        params.append(f"page={page}")
        params.append(f"per_page={per_page}")
        params.append("order=type")
        path = f"/zones/{zone_id}/dns_records?{'&'.join(params)}"
        return self._request("GET", path)

    def create_dns_record(self, zone_id, record_type, name, content, proxied=False, ttl=1):
        return self._request("POST", f"/zones/{zone_id}/dns_records", {
            "type": record_type,
            "name": name,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        })

    def update_dns_record(self, zone_id, record_id, data):
        return self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", data)

    def delete_dns_record(self, zone_id, record_id):
        return self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")

    def set_ssl_mode(self, zone_id, mode="flexible"):
        """Set zone SSL/TLS encryption mode. Modes: off, flexible, full, strict."""
        return self._request("PATCH", f"/zones/{zone_id}/settings/ssl", {"value": mode})

    def get_ssl_mode(self, zone_id):
        """Get current SSL/TLS encryption mode for a zone."""
        return self._request("GET", f"/zones/{zone_id}/settings/ssl")


    # ---- Workers (mirror proxy) ----
    def upload_worker(self, account_id, worker_name, script):
        """Upload a Worker script. Creates or updates."""
        return self._request(
            "PUT",
            f"/accounts/{account_id}/workers/scripts/{worker_name}",
            data=script,
            extra_headers={"Content-Type": "application/javascript"},
        )

    def delete_worker(self, account_id, worker_name):
        """Delete a Worker script."""
        return self._request("DELETE", f"/accounts/{account_id}/workers/scripts/{worker_name}")

    def create_worker_route(self, zone_id, pattern, worker_name):
        """Create a Worker route for a zone."""
        return self._request(
            "POST",
            f"/zones/{zone_id}/workers/routes",
            {"pattern": pattern, "script": worker_name},
        )

    def list_worker_routes(self, zone_id):
        """List all Worker routes for a zone."""
        return self._request("GET", f"/zones/{zone_id}/workers/routes")

    def delete_worker_route(self, zone_id, route_id):
        """Delete a Worker route."""
        return self._request("DELETE", f"/zones/{zone_id}/workers/routes/{route_id}")

    # ---- Page Rules (302 redirect mirror) ----

    def create_page_rule(self, zone_id, pattern, target_url, status_code=302, cache_bypass=False):
        """Create a Page Rule.
        If cache_bypass=True: creates a bypass rule (no forwarding, just cache bypass).
        Otherwise: creates a Forwarding URL page rule.
        pattern like *domain.com/*, target like https://target.com/$2
        """
        if cache_bypass:
            payload = {
                "targets": [{"target": "url", "constraint": {"operator": "matches", "value": pattern}}],
                "actions": [{"id": "cache_level", "value": "bypass"}],
                "status": "active",
            }
        else:
            payload = {
                "targets": [{"target": "url", "constraint": {"operator": "matches", "value": pattern}}],
                "actions": [{"id": "forwarding_url", "value": {"url": target_url, "status_code": status_code}}],
                "status": "active",
            }
        return self._request("POST", f"/zones/{zone_id}/pagerules", payload)

    def list_page_rules(self, zone_id):
        """List all page rules in a zone."""
        return self._request("GET", f"/zones/{zone_id}/pagerules")

    def delete_page_rule(self, zone_id, rule_id):
        """Delete a page rule."""
        return self._request("DELETE", f"/zones/{zone_id}/pagerules/{rule_id}")

# Singleton instance
cf_client = CloudflareClient()
