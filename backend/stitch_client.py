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
            "SELECT config_value FROM global_config WHERE config_key = 'stitch_tokens'"
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
            "INSERT OR REPLACE INTO global_config (id, config_key, config_value, updated_at) "
            "VALUES (COALESCE((SELECT id FROM global_config WHERE config_key='stitch_tokens'), "
            "(SELECT COALESCE(MAX(id),0)+1 FROM global_config)), 'stitch_tokens', ?, datetime('now'))",
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

    def _force_refresh(self):
        """Force refresh access token (called on 401 retry)."""
        if not self._refresh_token:
            self._access_token, self._refresh_token = get_stitch_token()
        if self._refresh_token:
            self._access_token = _refresh_access_token(self._refresh_token)
            if self._access_token:
                save_stitch_token(self._access_token, self._refresh_token)
                return True
        return False

    def _rest_authorized(self, method, url, **kwargs):
        """Make an authenticated REST call with automatic 401 retry.
        On first 401: force-refresh the access token and retry once.
        Returns (status_code, response_text)."""
        if not self._ensure_token():
            return None, "Not authenticated"
        for attempt in (1, 2):
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                resp = http_requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 30), **kwargs)
                if resp.status_code != 401:
                    return resp.status_code, resp.text
                # 401 on first attempt → refresh & retry
                if attempt == 1 and self._force_refresh():
                    logger.info("Stitch token refreshed after 401, retrying...")
                    continue
            except Exception as e:
                return None, str(e)
        return 401, "Unauthorized after retry"

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
                timeout=90,
            )
            # Handle HTTP-level 401
            if resp.status_code == 401:
                # Token expired — ensure refresh_token loaded, refresh and retry once
                if not self._refresh_token:
                    _, self._refresh_token = get_stitch_token()
                logger.info("MCP 401: refreshing token (len=%d)...", len(self._refresh_token or ""))
                self._access_token = _refresh_access_token(self._refresh_token)
                if self._access_token:
                    save_stitch_token(self._access_token, self._refresh_token)
                    logger.info("MCP 401: token refreshed, retrying...")
                    return self._mcp_call(method, params)
                return {"error": "Stitch token expired, refresh failed."}

            # Parse JSON response
            try:
                data = resp.json()
            except Exception:
                logger.warning("MCP non-JSON response (HTTP %d): %s", resp.status_code, resp.text[:200])
                return {"raw": resp.text}

            # Handle JSON-RPC level auth errors (isError with auth message)
            result_block = data.get("result", {})
            if result_block.get("isError"):
                content = result_block.get("content", [])
                err_text = content[0].get("text", "") if content else ""
                if "authentication" in err_text.lower() or "unauthorized" in err_text.lower() or "missing" in err_text.lower():
                    logger.info("MCP auth error in response: %s", err_text[:120])
                    if not self._refresh_token:
                        _, self._refresh_token = get_stitch_token()
                    self._access_token = _refresh_access_token(self._refresh_token)
                    if self._access_token:
                        save_stitch_token(self._access_token, self._refresh_token)
                        logger.info("MCP auth error: token refreshed, retrying...")
                        return self._mcp_call(method, params)
                return {"error": err_text}

            return data
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
            err = resp["error"]
            if isinstance(err, dict):
                return {"error": err.get("message", str(err))}
            return {"error": str(err)}
        result = resp.get("result", {})
        content = result.get("content", [])
        if content and content[0].get("text"):
            text = content[0]["text"]
            try:
                return json.loads(text)
            except (json.JSONDecodeError, KeyError):
                # Log raw to debug Stitch response format
                logger.info(f"MCP raw text ({len(text)} chars): {text[:500]}")
                return {"raw": text}
        return result.get("structuredContent", result)

    # ---- High-level API ----
    def list_projects(self):
        """List all Stitch projects. Returns list of {name, displayName} dicts."""
        status, text = self._rest_authorized("GET", "https://stitch.googleapis.com/v1/projects")
        if status == 200 and text:
            try:
                data = json.loads(text)
                return data.get("projects", [])
            except Exception:
                return []
        return []

    def find_project_by_title(self, title):
        """Find an existing Stitch project by displayName. Returns project_id or None."""
        projects = self.list_projects()
        for p in projects:
            if p.get("displayName") == title:
                return p.get("name", "").split("/")[-1]
        return None

    def create_project(self, title):
        """Create a Stitch TEXT_TO_UI project via REST API. Returns numeric project ID."""
        status, text = self._rest_authorized(
            "POST", "https://stitch.googleapis.com/v1/projects",
            json={"displayName": title, "projectType": "TEXT_TO_UI", "visibility": "PRIVATE"},
        )
        if status == 200 and text:
            try:
                return json.loads(text).get("name", "").split("/")[-1]
            except Exception:
                return None
        logger.warning(f"create_project failed: {status} {text[:200] if text else ''}")
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
            logger.warning(f"generate_screen MCP error: {result['error'][:120]}")
            return None
        if isinstance(result, str):
            logger.warning(f"generate_screen returned string: {result[:120]}")
            return None
        # Parse response structure
        try:
            # Handle raw text responses (MCP JSON parse failure)
            if "raw" in result:
                raw = result["raw"]
                # Try re-parsing as JSON
                if raw.strip().startswith("{"):
                    try:
                        result = json.loads(raw)
                    except Exception:
                        pass

            components = result.get("outputComponents", [])
            if not components:
                logger.warning(f"generate_screen: no outputComponents, keys={list(result.keys())[:5]}, preview={str(result)[:200]}")
                return None
            for i, comp in enumerate(components):
                design = comp.get("design", {})
                screens = design.get("screens", [])
                if screens:
                    screen = screens[0]
                    return {
                        "screenId": screen.get("id", ""),
                        "htmlUrl": screen.get("htmlCode", {}).get("downloadUrl", ""),
                        "imageUrl": screen.get("screenshot", {}).get("downloadUrl", ""),
                        "sessionId": result.get("sessionId", ""),
                    }
            logger.warning(f"generate_screen: no screens in {len(components)} components")
            return None
        except Exception as e:
            logger.warning(f"generate_screen parse error: {e}")
            return None

    def _get_latest_screen_html(self, project_id):
        """Get the most recent screen's HTML from a project. Used as retry when generate_screen doesn't return htmlUrl."""
        try:
            screens = self.list_screens(project_id)
            if screens and len(screens) > 0:
                latest = screens[-1]  # Most recently created
                return {
                    "screenId": latest.get("id", ""),
                    "htmlUrl": latest.get("htmlCode", {}).get("downloadUrl", ""),
                    "imageUrl": latest.get("screenshot", {}).get("downloadUrl", ""),
                }
        except Exception:
            pass
        return {}

    def download_screen_html(self, html_url):
        """Download HTML from signed URL. Checks HTTP 200 + Content-Length > 0.
        Stitch only serves the file when rendering is complete. Returns HTML or None."""
        if not html_url:
            return None
        try:
            resp = http_requests.get(html_url, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 0:
                return resp.text
            logger.warning(f"download_screen_html: HTTP {resp.status_code}, {len(resp.content)} bytes")
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
        status, text = self._rest_authorized(
            "GET", f"https://stitch.googleapis.com/v1/{screen_name}/code",
        )
        if status == 200 and text:
            try:
                data = json.loads(text)
                return data.get("content") or data.get("html") or ""
            except Exception:
                return None
        return None

    # ---- Store Design Generation ----
    def generate_store_design(self, brand_kit, pages=None, progress_callback=None, on_screen=None):
        """Generate a complete e-commerce store design from brand kit data.

        Args:
            brand_kit: Brand kit dict (may contain stitch_screens mapping)
            pages: List of page types to generate
            progress_callback: callable(str) for progress updates
            on_screen: callable(page_type, screen_id) called after each screen save

        Returns:
            dict: {"screens": {page_type: html}, "project_id": str, "screen_ids": {page_type: screen_id}}
            or None on failure
        """
        if not self.is_authenticated:
            logger.warning("Stitch not authenticated")
            return None
        if pages is None:
            pages = ["home", "product", "cart", "checkout"]

        # --- Extract brand data ---
        brand_name = brand_kit.get("brand_name") or brand_kit.get("name", "Store")
        industry = brand_kit.get("industry", "lifestyle")
        description = brand_kit.get("description", "")
        colors = brand_kit.get("colors", [])
        primary = colors[0] if colors else "#1a1a2e"
        accent = colors[1] if len(colors) > 1 else "#c9a96e"

        # Business info
        biz = brand_kit.get("business_info") or {}
        city = biz.get("city", "New York")
        state = biz.get("state_code", "NY")

        # Footer/contact
        footer = brand_kit.get("footer_config") or {}
        store_address = footer.get("address", "")
        store_phone = footer.get("phone", "")
        store_email = footer.get("email", "")

        # Woo config
        woo = brand_kit.get("woo_config") or {}
        postcode = woo.get("postcode", biz.get("postcode", "10001"))

        # Design style
        design_system = brand_kit.get("design_system") or {}
        layout = design_system.get("layout", {})
        hero = layout.get("hero", {})
        headline = hero.get("headline", f"Welcome to {brand_name}")

        # --- Build style description ---
        style_desc = (
            f"Color scheme: primary={primary}, accent={accent}. "
            f"Professional {industry} brand aesthetic. "
            f"{description[:200] if description else 'Modern minimalist design with clean lines and generous whitespace.'} "
            f"Typography: elegant serif headings, clean sans-serif body. "
        )

        # --- Contact info string ---
        contact_info = (
            f"Store address: {store_address}, Phone: {store_phone}, Email: {store_email}"
            if store_address else f"Location: {city}, {state} {postcode}"
        )

        try:
            # 1. Find or create project (reuse existing for same brand_kit)
            project_id = brand_kit.get("stitch_project_id", "").strip()
            if project_id:
                logger.info(f"Stitch project REUSED: {project_id} for {brand_name}")
            else:
                project_id = self.create_project(f"{brand_name} Store")
                if not project_id:
                    return None
                logger.info(f"Stitch project CREATED: {project_id} for {brand_name}")

            # 2. Build prompts dynamically
            prompts = {
                "home": (
                    f"Complete e-commerce homepage for {brand_name}, a {industry} brand based in {city}, {state}. "
                    f"Headline: \"{headline}\". {style_desc} "
                    f"Include: sticky navigation with logo and cart icon, hero banner with headline and Shop Now CTA button, "
                    f"featured products grid (4 columns with images, titles, prices), trust badges (free shipping, secure checkout, easy returns), "
                    f"and a full footer with: {contact_info}. "
                    f"Luxury clean style, use {primary} and {accent} throughout the design."
                ),
                "product": (
                    f"Product detail page for {brand_name}. "
                    f"Two-column layout: left side image gallery with thumbnails, right side: product title, price, "
                    f"description, quantity selector, Add to Cart button (using {primary}), SKU, share icons. "
                    f"Below: product tabs (Description/Reviews), related products grid. "
                    f"{style_desc} Use {primary} for buttons and {accent} for accents."
                ),
                "cart": (
                    f"Shopping cart page for {brand_name}. "
                    f"Product rows with thumbnails, names, prices, quantity +/- controls, remove buttons. "
                    f"Order summary sidebar/bottom: subtotal, shipping estimate, Checkout button ({primary}). "
                    f"Empty cart state with Continue Shopping link. {style_desc}"
                ),
                "checkout": (
                    f"Checkout page for {brand_name}. "
                    f"Contact info form, shipping address form (pre-filled with {city}, {state} {postcode}), "
                    f"payment section, order summary. Secure lock icon. "
                    f"Place Order button in {primary}. {style_desc}"
                ),
                "order": (
                    f"Order confirmation / Thank You page for {brand_name}. "
                    f"Success checkmark icon, order number placeholder, order details summary, "
                    f"shipping to: {store_address or f'{city}, {state} {postcode}'}. "
                    f"Estimated delivery: 5-7 business days. Continue Shopping button ({primary}). {style_desc}"
                ),
                "about": (
                    f"About Us page for {brand_name}, a {industry} brand from {city}, {state}. "
                    f"Brand story, mission statement, core values with icons, "
                    f"{'team section,' if description else ''} timeline. "
                    f"Typography-focused layout. Contact: {store_email or ''}. {style_desc}"
                ),
                "contact": (
                    f"Contact page for {brand_name}. "
                    f"Contact form (name, email, subject, message). Store info: {contact_info}. "
                    f"Business hours: Mon-Fri 9AM-6PM EST. Map placeholder. {style_desc}"
                ),
                "faq": (
                    f"FAQ page for {brand_name}. "
                    f"Accordion Q&A grouped by: Orders & Payment, Shipping & Delivery, "
                    f"Returns & Exchanges, Product Care. Search bar at top. {style_desc}"
                ),
                "privacy": (
                    f"Privacy Policy page for {brand_name}. "
                    f"Professional layout: Information We Collect, How We Use It, Cookies, "
                    f"Third-Party Sharing, Your Rights, Contact Us ({store_email}). "
                    f"Last updated: {time.strftime('%B %Y')}. {style_desc}"
                ),
                "terms": (
                    f"Terms of Service for {brand_name}. "
                    f"Professional legal layout: Acceptance of Terms, Products & Pricing, "
                    f"Payment, Shipping, Returns, Limitation of Liability, Contact. "
                    f"Last updated: {time.strftime('%B %Y')}. {style_desc}"
                ),
                "shipping": (
                    f"Shipping Information for {brand_name}. "
                    f"Shipping rates table (Standard/Express/International), delivery timeframes, "
                    f"order tracking info, shipping restrictions. {style_desc}"
                ),
                "returns": (
                    f"Returns & Refunds for {brand_name}. "
                    f"30-day return policy, step-by-step return process, refund timeline (5-10 business days), "
                    f"exchange info, non-returnable items list. {style_desc}"
                ),
            }

            screens = {}
            screen_ids = {}
            # Load previously saved screen IDs for this brand_kit
            existing_ids = {}
            try:
                raw = brand_kit.get("stitch_screens", "")
                existing_ids = json.loads(raw) if raw and raw.strip() else {}
            except (json.JSONDecodeError, TypeError):
                existing_ids = {}

            page_names = {"home":"首页","product":"产品页","cart":"购物车","checkout":"结账页",
                          "order":"订单确认","about":"关于我们","contact":"联系我们","faq":"FAQ",
                          "privacy":"隐私政策","terms":"服务条款","shipping":"配送信息","returns":"退换政策"}
            total = len(pages)
            for idx, page_type in enumerate(pages, 1):
                prompt = prompts.get(page_type)
                if not prompt:
                    continue
                cn_name = page_names.get(page_type, page_type)
                progress_tag = f"({idx}/{total})"

                # Try to reuse existing screen first
                existing_sid = existing_ids.get(page_type, "")
                if existing_sid:
                    if progress_callback:
                        progress_callback(f"Stitch {progress_tag} 复用已有{cn_name}...")
                    html = self.get_screen_code(f"projects/{project_id}/screens/{existing_sid}")
                    if html and len(html) > 500:
                        screens[page_type] = html
                        screen_ids[page_type] = existing_sid
                        if progress_callback:
                            progress_callback(f"Stitch {progress_tag} {cn_name}复用完成 ({len(html)//1024}KB)")
                        if on_screen:
                            on_screen(page_type, existing_sid)
                        continue
                    else:
                        if progress_callback:
                            progress_callback(f"Stitch {progress_tag} {cn_name}已过期，重新生成...")

                if progress_callback:
                    progress_callback(f"Stitch {progress_tag} 正在生成{cn_name}...")

                result = self.generate_screen(project_id, prompt)
                if not result or result.get("error"):
                    logger.warning(f"Stitch {page_type}: skipped")
                    continue

                html_url = result.get("htmlUrl", "")
                screen_id = result.get("screenId", "")
                if not html_url:
                    # HTML not ready yet — poll via list_screens (max 3 attempts, 3s apart)
                    for retry in range(3):
                        time.sleep(3)
                        latest = self._get_latest_screen_html(project_id)
                        html_url = latest.get("htmlUrl", "")
                        screen_id = latest.get("screenId", screen_id)
                        if html_url:
                            break
                    if not html_url:
                        logger.warning(f"Stitch {page_type}: htmlUrl still unavailable after retries")

                if html_url:
                    # Download and verify: signed URL returns 200 + Content-Length when render complete
                    html = self.download_screen_html(html_url)
                    if html:
                        screens[page_type] = html
                        screen_ids[page_type] = screen_id
                        if progress_callback:
                            progress_callback(f"Stitch {progress_tag} {cn_name}完成 ({len(html)//1024}KB)")
                        # Immediately save this screen_id via callback
                        if on_screen:
                            on_screen(page_type, screen_id)
                    else:
                        logger.warning(f"Stitch {page_type}: download returned empty/error")

            return {"screens": screens, "project_id": project_id, "screen_ids": screen_ids} if screens else None

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
