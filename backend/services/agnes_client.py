"""Agnes AI Client — fast e-commerce page generation via Agne's AI API.

Much faster than Stitch MCP (~3-5s per page vs 30-60s).
Uses Agne's AI chat/completions endpoint to generate complete HTML pages.

Usage:
    from services.agnes_client import AgnesClient
    client = AgnesClient()
    html = client.generate_page("home", brand_kit)
"""

import json
import logging
import requests as http_requests

logger = logging.getLogger(__name__)

AGNES_BASE_URL = "https://apihub.agnes-ai.com/v1"
AGNES_MODEL = "agnes-2.0-flash"


def get_agnes_api_key():
    """Get Agne's AI API key from global_config."""
    try:
        from models import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT config_value FROM global_config WHERE config_key = 'agnes_api_key'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            data = json.loads(row[0])
            if isinstance(data, list) and data:
                return data[0]
            return row[0].strip()
    except Exception:
        pass
    return ""


class AgnesClient:
    """Client for Agne's AI page generation."""

    def __init__(self, api_key=None):
        self._api_key = api_key or get_agnes_api_key()

    @property
    def is_available(self):
        return bool(self._api_key)

    def _call_api(self, messages, temperature=0.3, max_tokens=8000):
        """Call Agne's AI chat/completions endpoint."""
        resp = http_requests.post(
            f"{AGNES_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": AGNES_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            body = resp.json()
            return body["choices"][0]["message"]["content"].strip()
        logger.warning(f"Agnes API error: {resp.status_code} {resp.text[:200]}")
        return None

    def generate_page(self, page_type, brand_kit, products=None):
        """Generate a single e-commerce page HTML.

        Args:
            page_type: one of home, product, cart, checkout, order, about,
                      contact, faq, privacy, terms, shipping, returns
            brand_kit: brand kit dict with brand_name, industry, colors, etc.
            products: list of product dicts (for home/product pages)

        Returns:
            HTML string or None
        """
        if not self._api_key:
            return None

        brand_name = brand_kit.get("brand_name") or brand_kit.get("name", "Store")
        industry = brand_kit.get("industry", "lifestyle")
        description = brand_kit.get("description", "")
        colors = brand_kit.get("colors", [])
        primary = colors[0] if colors else "#1a1a2e"
        accent = colors[1] if len(colors) > 1 else "#c9a96e"

        biz = brand_kit.get("business_info") or {}
        city = biz.get("city", "New York")
        state = biz.get("state_code", "NY")
        postcode = biz.get("postcode", "10001")

        footer = brand_kit.get("footer_config") or {}
        store_address = footer.get("address", "")
        store_phone = footer.get("phone", "")
        store_email = footer.get("email", "")

        contact_info = (
            f"{store_address}, Phone: {store_phone}, Email: {store_email}"
            if store_address else f"Location: {city}, {state} {postcode}"
        )

        # Build page-specific prompts
        prompts = {
            "home": (
                f"Create a complete e-commerce homepage for '{brand_name}', a {industry} brand.\n"
                f"Colors: primary={primary}, accent={accent}. {description[:200]}\n"
                f"Location: {city}, {state}. Contact: {contact_info}\n\n"
                f"Include: sticky nav with logo+cart icon, hero banner with headline and Shop Now CTA, "
                f"featured products grid (4 cols), trust badges (free shipping/secure checkout/easy returns), "
                f"full footer with contact info.\n"
                f"Use THEME_COLOR for buttons and ACCENT_COLOR for highlights.\n"
                f"Return ONLY valid HTML (no markdown, no explanation). Include inline CSS in <style> tag."
            ),
            "product": (
                f"Create a product detail page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Two-column layout: left image gallery with thumbnails, right side: product title, price, "
                f"description, quantity selector, Add to Cart button, SKU, share icons.\n"
                f"Below: product tabs (Description/Reviews), related products grid.\n"
                f"Return ONLY valid HTML with inline CSS."
            ),
            "cart": (
                f"Create a shopping cart page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Product rows with thumbnails, names, prices, quantity controls, remove buttons.\n"
                f"Order summary: subtotal, shipping estimate, Checkout button.\n"
                f"Empty cart state with Continue Shopping link.\n"
                f"Return ONLY valid HTML with inline CSS."
            ),
            "checkout": (
                f"Create a checkout page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Contact info form, shipping address form (pre-filled: {city}, {state} {postcode}), "
                f"payment section, order summary, Place Order button.\n"
                f"Return ONLY valid HTML with inline CSS."
            ),
            "order": (
                f"Create an order confirmation page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Success checkmark, order number, details summary, "
                f"shipping to: {contact_info}, estimated delivery 5-7 days.\n"
                f"Continue Shopping button. Return ONLY valid HTML with inline CSS."
            ),
            "about": (
                f"Create an About Us page for '{brand_name}', a {industry} brand from {city}, {state}.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Brand story, mission statement, core values with icons, timeline.\n"
                f"Typography-focused layout. Contact: {store_email or ''}.\n"
                f"Return ONLY valid HTML with inline CSS."
            ),
            "contact": (
                f"Create a contact page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Contact form (name, email, subject, message). Store info: {contact_info}.\n"
                f"Business hours: Mon-Fri 9AM-6PM. Return ONLY valid HTML with inline CSS."
            ),
            "faq": (
                f"Create a FAQ page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Accordion Q&A: Orders & Payment, Shipping & Delivery, Returns & Exchanges, Product Care.\n"
                f"Search bar at top. Return ONLY valid HTML with inline CSS."
            ),
            "privacy": (
                f"Create a Privacy Policy page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Professional legal layout: Information We Collect, How We Use It, Cookies, "
                f"Third-Party Sharing, Your Rights, Contact Us ({store_email}).\n"
                f"Return ONLY valid HTML with inline CSS."
            ),
            "terms": (
                f"Create a Terms of Service page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Professional legal layout: Acceptance, Products & Pricing, Payment, Shipping, Returns, "
                f"Limitation of Liability, Contact. Return ONLY valid HTML with inline CSS."
            ),
            "shipping": (
                f"Create a Shipping Information page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"Shipping rates table (Standard/Express/Intl), delivery timeframes, "
                f"tracking info, restrictions. Return ONLY valid HTML with inline CSS."
            ),
            "returns": (
                f"Create a Returns & Refunds page for '{brand_name}'.\n"
                f"Colors: primary={primary}, accent={accent}.\n"
                f"30-day return policy, step-by-step process, refund timeline (5-10 days), "
                f"exchange info, non-returnable items list. Return ONLY valid HTML with inline CSS."
            ),
        }

        prompt = prompts.get(page_type)
        if not prompt:
            return None

        messages = [
            {"role": "system", "content": (
                "You are a professional e-commerce web designer. "
                "Create complete, production-ready HTML pages with embedded CSS. "
                "Use THEME_COLOR and ACCENT_COLOR as color variables. "
                "Design should be modern, clean, and responsive. "
                "NEVER include markdown fences (```html) or explanations. "
                "Output MUST start with <!DOCTYPE html> and contain only valid HTML."
            )},
            {"role": "user", "content": prompt.replace("THEME_COLOR", primary).replace("ACCENT_COLOR", accent)},
        ]

        html = self._call_api(messages)
        if not html:
            return None

        # Strip markdown fences if present
        html = html.strip()
        if html.startswith("```"):
            lines = html.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            html = "\n".join(lines)

        return html

    def generate_store_design(self, brand_kit, pages=None, progress_callback=None):
        """Generate complete store design for all page types.

        Args:
            brand_kit: Brand kit dict
            pages: List of page types (default: all 12)
            progress_callback: callable(str) for progress updates

        Returns:
            dict: {page_type: html_content} or None
        """
        if not self._api_key:
            return None

        if pages is None:
            pages = ["home", "product", "cart", "checkout", "order",
                     "about", "contact", "faq", "privacy", "terms", "shipping", "returns"]

        page_names = {
            "home": "首页", "product": "产品页", "cart": "购物车", "checkout": "结账页",
            "order": "订单确认", "about": "关于我们", "contact": "联系我们", "faq": "FAQ",
            "privacy": "隐私政策", "terms": "服务条款", "shipping": "配送信息", "returns": "退换政策",
        }

        screens = {}
        total = len(pages)
        for idx, page_type in enumerate(pages, 1):
            cn_name = page_names.get(page_type, page_type)
            if progress_callback:
                progress_callback(f"Agnes AI ({idx}/{total}) 正在生成{cn_name}...")

            html = self.generate_page(page_type, brand_kit)
            if html and len(html) > 500:
                screens[page_type] = html
                if progress_callback:
                    progress_callback(f"Agnes AI ({idx}/{total}) {cn_name}完成 ({len(html)//1024}KB)")
            else:
                logger.warning(f"Agnes {page_type}: empty/short response")

        return screens if screens else None


def verify_agnes_key(api_key):
    """Verify an Agne's AI API key by making a test call."""
    try:
        resp = http_requests.post(
            f"{AGNES_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": AGNES_MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return {"success": True, "message": "Agnes API 密钥有效"}
        return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}
