"""Stitch MCP Client — design generation via Google Stitch API.

Flow:
  1. One-time OAuth login → refresh token stored in DB
  2. Access token auto-refreshed per session
  3. MCP protocol calls: create_project → generate_screens → get HTML
  4. Falls back gracefully if token missing / expired

Usage:
  from stitch_client import StitchClient
  stitch = StitchClient()
  html = stitch.generate_store_design(brand_name="MyBrand", pages=["home","product","cart"])
"""

import json
import logging
import sqlite3
import os
import time
import re

import requests as http_requests

logger = logging.getLogger(__name__)

STITCH_MCP_URL = "https://stitch.googleapis.com/mcp"
OAUTH_CLIENT_ID = os.environ.get("STITCH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.environ.get("STITCH_CLIENT_SECRET", "")
OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform", "openid"]
REDIRECT_PORT = 8085

# ---------------------------------------------------------------------------
# Token storage (global_config table)
# ---------------------------------------------------------------------------
def _get_db():
    from models import get_db as _gdb
    return _gdb()


def get_stitch_token():
    """Return (access_token, refresh_token) from DB, or (None, None)."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT value FROM global_config WHERE key = 'stitch_tokens'"
        ).fetchone()
        if row and row[0]:
            data = json.loads(row[0])
            return data.get("access_token"), data.get("refresh_token")
        return None, None
    finally:
        conn.close()


def save_stitch_token(access_token, refresh_token):
    """Persist tokens to global_config."""
    conn = _get_db()
    try:
        data = json.dumps({"access_token": access_token, "refresh_token": refresh_token})
        conn.execute(
            "INSERT OR REPLACE INTO global_config (key, value, updated_at) VALUES ('stitch_tokens', ?, datetime('now'))",
            (data,),
        )
        conn.commit()
    finally:
        conn.close()


def save_stitch_refresh_token(refresh_token):
    """Save only the refresh token (long-lived)."""
    save_stitch_token("", refresh_token)


# ---------------------------------------------------------------------------
# OAuth token refresh
# ---------------------------------------------------------------------------
def _refresh_access_token(refresh_token):
    """Exchange refresh token for new access token. Returns access_token or None."""
    try:
        resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("access_token")
        logger.warning(f"Token refresh failed: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        logger.warning(f"Token refresh error: {e}")
        return None


# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------
class StitchClient:
    """Client for Google Stitch MCP API with automatic token management."""

    def __init__(self, access_token=None, refresh_token=None):
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._session_initialized = False
        self._request_id = 0

    # ---- Auth ----
    def _ensure_token(self):
        """Ensure we have a valid access token. Returns True if ready."""
        if self._access_token:
            return True
        if not self._refresh_token:
            self._access_token, self._refresh_token = get_stitch_token()
        if self._refresh_token and not self._access_token:
            self._access_token = _refresh_access_token(self._refresh_token)
            if self._access_token:
                save_stitch_token(self._access_token, self._refresh_token)
        return bool(self._access_token)

    @property
    def is_authenticated(self):
        return self._ensure_token()

    # ---- MCP calls ----
    def _mcp_call(self, method, params=None):
        """Send a JSON-RPC call to the Stitch MCP endpoint."""
        if not self._ensure_token():
            return {"error": "Stitch not authenticated. Run stitch OAuth login first."}

        self._request_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        try:
            resp = http_requests.post(
                STITCH_MCP_URL,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=60,
            )
            if resp.status_code == 401:
                # Token expired — refresh and retry once
                self._access_token = _refresh_access_token(self._refresh_token)
                if self._access_token:
                    save_stitch_token(self._access_token, self._refresh_token)
                    return self._mcp_call(method, params)
                return {"error": "Stitch token expired, refresh failed."}
            return resp.json()
        except Exception as e:
            logger.error(f"Stitch MCP call failed: {e}")
            return {"error": str(e)}

    def _init_session(self):
        """Initialize MCP session (required once per client)."""
        if self._session_initialized:
            return True
        self._mcp_call("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "kairui", "version": "1.0"},
        })
        self._mcp_call("notifications/initialized")
        self._session_initialized = True
        return True

    def _call_tool(self, tool_name, arguments):
        """Call an MCP tool and return parsed result."""
        self._init_session()
        resp = self._mcp_call("tools/call", {"name": tool_name, "arguments": arguments})
        if "error" in resp:
            return {"error": resp.get("error", {}).get("message", str(resp))}
        result = resp.get("result", {})
        content = result.get("content", [])
        if content and content[0].get("text"):
            try:
                return json.loads(content[0]["text"])
            except (json.JSONDecodeError, KeyError):
                return {"raw": content[0].get("text", "")}
        return result.get("structuredContent", result)

    # ---- High-level API ----
    def create_project(self, title):
        """Create a Stitch TEXT_TO_UI_PRO project via REST API. Returns numeric project ID."""
        if not self._ensure_token():
            return None
        try:
            resp = http_requests.post(
                "https://stitch.googleapis.com/v1/projects",
                headers={"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"},
                json={"displayName": title, "projectType": "TEXT_TO_UI_PRO", "visibility": "PRIVATE"},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("name", "").split("/")[-1]
            logger.warning(f"create_project failed: {resp.status_code} {resp.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"create_project error: {e}")
            return None

    def generate_screen(self, project_id, prompt, device_type="DESKTOP"):
        """Generate a screen. Returns {"screenId":..., "htmlUrl":..., "imageUrl":...} or None."""
        numeric_id = project_id.replace("projects/", "")
        result = self._call_tool("generate_screen_from_text", {
            "projectId": numeric_id,
            "prompt": prompt,
            "deviceType": device_type,
        })
        if "error" in result:
            return None
        # Parse response structure
        try:
            components = result.get("outputComponents", [])
            if not components:
                return None
            design = components[0].get("design", {})
            screens = design.get("screens", [])
            if not screens:
                return None
            screen = screens[0]
            html_url = screen.get("htmlCode", {}).get("downloadUrl", "")
            image_url = screen.get("screenshot", {}).get("downloadUrl", "")
            screen_id = screen.get("id", "")
            return {
                "screenId": screen_id,
                "htmlUrl": html_url,
                "imageUrl": image_url,
                "sessionId": result.get("sessionId", ""),
            }
        except Exception:
            return None

    def download_screen_html(self, html_url):
        """Download HTML content from signed URL (no auth needed). Returns HTML string or None."""
        if not html_url:
            return None
        try:
            # Signed URLs from contribution.usercontent.google.com don't need auth
            resp = http_requests.get(html_url, timeout=30)
            if resp.status_code == 200:
                return resp.text
            logger.warning(f"download_screen_html: {resp.status_code}")
            return None
        except Exception as e:
            logger.warning(f"download_screen_html failed: {e}")
            return None

    def get_screen(self, project_id, screen_id):
        """Get screen details including HTML."""
        result = self._call_tool("get_screen", {
            "projectId": project_id,
            "screenId": screen_id,
        })
        return result

    def list_screens(self, project_id):
        """List all screens in a project."""
        result = self._call_tool("list_screens", {"projectId": project_id})
        return result.get("screens", [])

    def get_screen_code(self, screen_name):
        """Get the HTML/CSS code for a screen via REST API."""
        if not self._ensure_token():
            return None
        try:
            # screen_name is like "projects/xxx/screens/yyy"
            resp = http_requests.get(
                f"https://stitch.googleapis.com/v1/{screen_name}/code",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("content") or data.get("html") or ""
            return None
        except Exception as e:
            logger.warning(f"get_screen_code failed: {e}")
            return None

    # ---- Store Design Generation ----
    def generate_store_design(self, brand_name, pages=None):
        """Generate a complete e-commerce store design.

        Args:
            brand_name: Brand/company name
            pages: List of page types: home/product/cart/checkout/order/about/contact/faq/privacy/terms/shipping/returns

        Returns:
            dict: {page_type: html_content} or None on failure
        """
        if not self.is_authenticated:
            logger.warning("Stitch not authenticated, skip design generation")
            return None

        if pages is None:
            pages = ["home", "product", "cart", "checkout"]

        try:
            # 1. Create project
            project_id = self.create_project(f"{brand_name} Store")
            if not project_id:
                logger.warning("Stitch create_project failed")
                return None
            logger.info(f"Stitch project created: {project_id}")

            # 2. Generate screens — all page types
            page_prompts = {
                "home": (
                    f"Complete e-commerce homepage for {brand_name}. "
                    "Include: sticky navigation with logo and cart icon, hero banner with headline and CTA button, "
                    "featured products grid (4 columns with images, titles, prices), trust badges, "
                    "and a full footer with brand info, quick links, policies, and contact. "
                    "Luxury minimalist style, white background, elegant serif headings, dark navy text, gold accents."
                ),
                "product": (
                    f"Product detail page for {brand_name}. "
                    "Two-column layout: left side has image gallery with thumbnails, right side has "
                    "product title, price, description, quantity selector, Add to Cart button, "
                    "SKU, category, and share buttons. Below: product tabs (Description/Reviews), related products grid. "
                    "Same luxury minimalist style as homepage."
                ),
                "cart": (
                    f"Shopping cart page for {brand_name}. "
                    "Full-width table layout: product thumbnails, names, prices, quantity controls, remove buttons. "
                    "Right sidebar or bottom section with subtotal, shipping estimate, and Proceed to Checkout button. "
                    "Empty state when no items. Matching luxury minimalist style."
                ),
                "checkout": (
                    f"Checkout page for {brand_name}. "
                    "Multi-step or single-page layout: contact information form, shipping address form, "
                    "payment method section, order summary sidebar. "
                    "Secure and professional design with the same luxury minimalist aesthetic."
                ),
                "order": (
                    f"Order confirmation page for {brand_name}. "
                    "Thank you message, order number, order details summary, shipping address, "
                    "estimated delivery date, and Continue Shopping button. "
                    "Celebratory but clean design, matching the store aesthetic."
                ),
                "about": (
                    f"About Us page for {brand_name}. "
                    "Brand story, mission statement, values section with icons, team section, "
                    "and a timeline or history. Clean typography-focused layout. Same luxury style."
                ),
                "contact": (
                    f"Contact page for {brand_name}. "
                    "Contact form (name, email, subject, message), store address, phone, email, "
                    "business hours, and embedded map placeholder. Clean professional layout."
                ),
                "faq": (
                    f"FAQ page for {brand_name}. "
                    "Accordion-style questions and answers grouped by category (Orders, Shipping, Returns, Products). "
                    "Clean, scannable layout with search bar at top."
                ),
                "privacy": (
                    f"Privacy Policy page for {brand_name}. "
                    "Professional legal-style layout with clear headings, sections for data collection, "
                    "cookies, third-party sharing, user rights. Clean typography."
                ),
                "terms": (
                    f"Terms of Service page for {brand_name}. "
                    "Professional legal-style layout with clear sections, numbered clauses. "
                    "Clean typography matching the brand."
                ),
                "shipping": (
                    f"Shipping Information page for {brand_name}. "
                    "Shipping rates table, delivery timeframes, international shipping info, "
                    "tracking information. Clean organized layout."
                ),
                "returns": (
                    f"Returns & Refunds page for {brand_name}. "
                    "Return policy, step-by-step return process, refund timeline, exchange info. "
                    "Clean organized layout with the luxury brand aesthetic."
                ),
            }

            screens = {}
            for page_type in pages:
                prompt = page_prompts.get(page_type)
                if not prompt:
                    continue
                logger.info(f"Stitch generating {page_type} page...")
                result = self.generate_screen(project_id, prompt)

                if not result or result.get("error"):
                    logger.warning(f"Stitch {page_type}: {result.get('error','no result') if result else 'no result'}")
                    continue

                html_url = result.get("htmlUrl", "")
                if html_url:
                    time.sleep(2)
                    html = self.download_screen_html(html_url)
                    if html:
                        screens[page_type] = html
                        logger.info(f"Stitch {page_type}: {len(html)} chars")

            return screens if screens else None

        except Exception as e:
            logger.error(f"Stitch design generation failed: {e}")
            return None


# ---------------------------------------------------------------------------
# CLI OAuth login (run manually: python stitch_client.py --login)
# ---------------------------------------------------------------------------
def oauth_login():
    """Interactive OAuth login flow. Opens browser, captures callback, saves token."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed. Run: pip install google-auth-oauthlib")
        return False

    client_config = {
        "installed": {
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{REDIRECT_PORT}"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, OAUTH_SCOPES)
    credentials = flow.run_local_server(port=REDIRECT_PORT, open_browser=True)

    refresh_token = credentials.refresh_token
    access_token = credentials.token

    if refresh_token:
        save_stitch_token(access_token, refresh_token)
        print(f"\n✅ Stitch OAuth successful!")
        print(f"   Refresh token saved to DB")
        print(f"   Access token: {access_token[:20]}...")
        return True
    else:
        print("\n❌ No refresh token returned. Try again.")
        return False


if __name__ == "__main__":
    import sys

    if "--login" in sys.argv:
        oauth_login()
    else:
        print("Usage: python stitch_client.py --login")
