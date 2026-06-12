"""
WordPressAdminSession — Login to WP admin and read/write settings via REST API.

Usage::

    from services.wordpress_client import WordPressAdminSession
    wp = WordPressAdminSession("https://example.com", "admin", "password")
    wp.login()
    settings = wp.get_settings()
    wp.update_settings({"blogname": "My Site", "admin_email": "a@b.com"})
    woocommerce = wp.get_woocommerce_settings("general")
"""

import json as _json
import logging
import os
import re
import threading
import time
import requests as http_requests
import urllib3
from urllib.parse import urlparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)



class WordPressAdminSession:
    """Authenticated WordPress admin session for settings management."""

    def __init__(self, site_url: str, username: str, password: str, timeout: int = 120):
        self.site_url = site_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = http_requests.Session()
        self.session.verify = False
        self._logged_in_at = 0.0   # timestamp, 0 = never logged in
        self._cached_nonce = None
        self._cached_nonce_at = 0.0
        self._login_lock = threading.RLock()

    @property
    def _logged_in(self):
        """Session expires after 15 minutes. Auto-returns False if stale."""
        if not self._logged_in_at:
            return False
        if time.time() - self._logged_in_at > 900:
            self._logged_in_at = 0.0
            return False
        return True

    @_logged_in.setter
    def _logged_in(self, value):
        self._logged_in_at = time.time() if value else 0.0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """Log into WordPress admin and store cookies in session."""
        with self._login_lock:
            try:
                r = self.session.post(
                    f"{self.site_url}/wp-login.php",
                    data={
                        "log": self.username,
                        "pwd": self.password,
                        "wp-submit": "Log In",
                        "redirect_to": f"{self.site_url}/wp-admin/",
                    },
                    timeout=self.timeout,
                    allow_redirects=False,
                )

                # HTTP requests may get 302-redirected to HTTPS by the server;
                # login cookies won't stick when this happens.
                # Detect this and retry with the upgraded URL.
                if r.status_code in (301, 302, 307, 308) and self.site_url.startswith("http://"):
                    location = r.headers.get("Location", "")
                    if location.startswith("https://"):
                        parsed = urlparse(location)
                        new_url = f"https://{parsed.netloc}"
                        logger.info("WP login: HTTP→HTTPS redirect, upgrading %s → %s", self.site_url, new_url)
                        self.site_url = new_url
                        return self.login()

                if r.status_code in (302, 200):
                    # Check for auth cookies (more reliable than redirect status)
                    has_cookies = any(c.name.startswith("wordpress_logged_in") for c in r.cookies)
                    if r.status_code == 200 and not has_cookies:
                        # Login rejected — returned 200 but no auth cookies
                        content_len = len(r.content) if r.content else 0
                        body_hint = r.text[:200] if r.text else "(empty)"
                        # Check response headers for clues
                        headers_str = "; ".join(f"{k}={v}" for k, v in r.headers.items() if k.lower() in (
                            "content-type", "content-length", "x-php-fatal", "x-powered-by", "location", "server"
                        ))
                        logger.warning(
                            "WP login rejected: status=200, no auth cookie, content_len=%d, headers=[%s], body: %s",
                            content_len, headers_str, body_hint,
                        )
                    else:
                        # Verify by fetching admin page
                        r2 = self.session.get(f"{self.site_url}/wp-admin/", timeout=self.timeout)
                        admin_ok = "wp-admin-bar" in r2.text or "dashboard" in r2.text.lower()
                        if admin_ok:
                            self._logged_in = True
                            logger.info("WP login OK: %s", self.site_url)
                            return True
                        # Admin page didn't confirm login
                        body_hint = r2.text[:300] if r2.text else "(empty)"
                        logger.warning("WP login admin check failed: len=%d, preview: %s", len(r2.text), body_hint)
                        # Check for PHP fatal in headers
                        fatal = r2.headers.get("X-PHP-Fatal", "")
                        if fatal:
                            logger.warning("WP login admin page has PHP fatal: %s", fatal[:200])
                        # Plugin Activation Error — use kairui-dbg.php to fix (direct DB, bypasses admin)
                        if "Plugin Activation Error" in r2.text:
                            logger.warning("WP login: Plugin Activation Error, attempting recovery via kairui-dbg...")
                            try:
                                # Try deactivating all non-standard plugins (kairui-dbg supports any slug)
                                r_fix = self.session.get(
                                    f"{self.site_url}/kairui-dbg.php?s=kairui_import_2024&fix=woodmart-core",
                                    timeout=self.timeout,
                                )
                                logger.info("WP login recovery fix result: %s", r_fix.text[:200])
                                r_fix2 = self.session.get(
                                    f"{self.site_url}/kairui-dbg.php?s=kairui_import_2024&fix=woodmart-images-optimizer",
                                    timeout=self.timeout,
                                )
                                # Also try fixing woocommerce
                                r_fix3 = self.session.get(
                                    f"{self.site_url}/kairui-dbg.php?s=kairui_import_2024&fix=woocommerce",
                                    timeout=self.timeout,
                                )
                                # Retry admin check
                                r3 = self.session.get(f"{self.site_url}/wp-admin/", timeout=self.timeout)
                                if "wp-admin-bar" in r3.text or "dashboard" in r3.text.lower():
                                    self._logged_in = True
                                    logger.info("WP login OK after recovery: %s", self.site_url)
                                    return True
                                logger.warning("WP login recovery: admin still broken after fix, body=%s", r3.text[:200])
                            except Exception as _re:
                                logger.warning("WP login recovery failed: %s", _re)
                logger.warning("WP login failed: status=%s", r.status_code)
                return False
            except Exception as e:
                logger.warning("WP login error: %s", e)
                return False

    # ------------------------------------------------------------------
    # WordPress Settings (options-general.php)
    # ------------------------------------------------------------------

    # Map REST API field names to options.php field names
    _REST_TO_OPTION = {
        "title": "blogname",
        "description": "blogdescription",
        "email": "admin_email",
        "timezone": "timezone_string",
        "date_format": "date_format",
        "time_format": "time_format",
        "start_of_week": "start_of_week",
        "language": "WPLANG",
        "url": "siteurl",
        "default_category": "default_category",
        "default_post_format": "default_post_format",
        "posts_per_page": "posts_per_page",
        "use_smilies": "use_smilies",
    }

    def get_settings(self) -> dict:
        """Get WordPress settings. Returns normalized dict with both REST and option keys."""
        if not self._logged_in and not self.login():
            return {}
        try:
            r = self.session.get(
                f"{self.site_url}/wp-json/wp/v2/settings",
                timeout=self.timeout,
            )
            if r.status_code == 200:
                data = r.json()
                # Also get options that aren't in REST API
                extras = self._scrape_options_page()
                data["default_role"] = extras.get("default_role", "subscriber")
                data["users_can_register"] = extras.get("users_can_register", "0")
                data["admin_email"] = data.get("email", extras.get("admin_email", ""))
                logger.info("WP settings loaded via REST API")
                return data
            return self._scrape_options_page()
        except Exception as e:
            logger.warning("get_settings error: %s", e)
            return {}

    def _get_rest_nonce(self) -> str | None:
        """Extract the REST API nonce from a WordPress admin page.

        Nonce is cached for 5 minutes to avoid hitting the admin page on every
        REST API call.  Uses allow_redirects=False so that expired cookies
        produce a 302 → wp-login.php rather than silently overwriting the
        session cookie jar with login-page cookies (which would corrupt the
        session and cause wpcom_rest_api_status errors).
        """
        # Return cached nonce if still fresh (< 5 min)
        if self._cached_nonce and (time.time() - self._cached_nonce_at) < 300:
            return self._cached_nonce

        try:
            r = self.session.get(
                f"{self.site_url}/wp-admin/options-general.php",
                timeout=self.timeout,
                allow_redirects=False,
            )
            # Expired session → 302 redirect to wp-login.php
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                if "wp-login.php" in loc:
                    logger.info("_get_rest_nonce: redirected to login page, session may be stale")
                    self._cached_nonce = None
                    return None  # caller handles re-login via auth retry
            m = re.search(r'"nonce"\s*:\s*"([^"]+)"', r.text)
            if m:
                self._cached_nonce = m.group(1)
                self._cached_nonce_at = time.time()
                return self._cached_nonce
            m = re.search(r'name="_wpnonce"\s+value="([^"]+)"', r.text)
            if m:
                self._cached_nonce = m.group(1)
                self._cached_nonce_at = time.time()
                return self._cached_nonce
            # Page loaded but nonce not found — log for diagnosis
            logger.warning("_get_rest_nonce: nonce not found on page (status=%s, len=%d, preview[:200]=%s)",
                           r.status_code, len(r.text), r.text[:200])
        except Exception as e:
            logger.warning("_get_rest_nonce error: %s", e)
        return None

    def _get_setting(self, key: str) -> str | int | None:
        """Get a single WordPress option value via REST API."""
        try:
            rest_nonce = self._get_rest_nonce()
            headers = {"X-WP-Nonce": rest_nonce} if rest_nonce else {}
            r = self.session.get(
                f"{self.site_url}/wp-json/wp/v2/settings",
                headers=headers,
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return r.json().get(key)
        except Exception as e:
            logger.warning("_get_setting(%s) error: %s", key, e)
        return None

    def update_settings(self, data: dict) -> bool:
        """Update WordPress options. Try REST API with nonce first, fall back to options.php."""
        if not self._logged_in and not self.login():
            return False
        try:
            rest_data = {}
            option_data = {}
            rest_keys = {"title", "description", "email", "timezone", "timezone_string",
                         "date_format", "time_format", "start_of_week", "language",
                         "url", "default_category", "default_post_format", "posts_per_page",
                         "site_icon", "site_logo", "users_can_register", "default_role"}
            for k, v in data.items():
                if k in rest_keys or k in self._REST_TO_OPTION:
                    rest_data[k] = v
                else:
                    option_data[k] = v

            rest_ok = False
            if rest_data:
                headers = {}
                rest_nonce = self._get_rest_nonce()
                if rest_nonce:
                    headers["X-WP-Nonce"] = rest_nonce
                    logger.info("Using REST nonce: %s...", rest_nonce[:8])
                r = self.session.post(
                    f"{self.site_url}/wp-json/wp/v2/settings",
                    json=rest_data,
                    headers=headers,
                    timeout=self.timeout,
                )
                if r.status_code in (200, 201):
                    rest_ok = True
                    resp_body = r.text[:500]
                    logger.info("WP settings updated via REST (%s): keys=%s resp=%s",
                                r.status_code, list(rest_data.keys()), resp_body)
                else:
                    logger.info("REST settings failed (%s), resp=%s, retrying via options.php for: %s",
                                r.status_code, r.text[:300], list(rest_data.keys()))
                    for k, v in rest_data.items():
                        option_data.setdefault(k, v)

            if option_data or data.get("default_role") or data.get("users_can_register") is not None:
                merged = dict(data)
                merged.update(option_data)
                result = self._post_options_page(merged)
                logger.info("_post_options_page result: %s for keys: %s", result, list(merged.keys()))
                return result

            return rest_ok if rest_data else True
        except Exception as e:
            logger.warning("update_settings error: %s", e)
            return self._post_options_page(data)

    # ------------------------------------------------------------------
    # WooCommerce Settings
    # ------------------------------------------------------------------

    def is_woocommerce_active(self) -> bool:
        """Check if WooCommerce plugin is installed and active."""
        if not self._logged_in and not self.login():
            return False
        try:
            # Method 1: REST API
            r = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/system_status",
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return True
            # Method 2: Check admin plugins page for active WooCommerce
            logger.info("WC REST API returned %s, falling back to plugins page check", r.status_code)
            r2 = self.session.get(f"{self.site_url}/wp-admin/plugins.php", timeout=self.timeout)
            if "woocommerce" in r2.text.lower():
                # Check if it's active (not just installed)
                if 'deactivate>woocommerce<' in r2.text.lower() or 'woocommerce/woocommerce.php' in r2.text:
                    logger.info("WC detected via plugins page")
                    return True
            return False
        except Exception as e:
            logger.warning("WC status check error: %s", e)
            return False

    def ensure_woocommerce(self) -> bool:
        """Verify WooCommerce is active; install+activate if missing.

        Returns True if WooCommerce is confirmed active.
        """
        if self.is_woocommerce_active():
            logger.info("WC already active, skipping install")
            return True

        logger.info("WC not active, installing via upload...")
        import io

        slug = "woocommerce"
        # Check if plugin files exist but not active
        r_pl = self.session.get(f"{self.site_url}/wp-admin/plugins.php", timeout=15)
        if f'data-plugin="{slug}/' not in r_pl.text:
            # Download and upload the plugin
            dl_url = f"https://downloads.wordpress.org/plugin/{slug}.latest-stable.zip"
            r_dl = http_requests.get(dl_url, timeout=120, allow_redirects=True)
            if r_dl.status_code != 200 or len(r_dl.content) < 100:
                logger.error("WC download failed: HTTP %s, len=%s", r_dl.status_code, len(r_dl.content))
                return False
            logger.info("WC downloaded: %d bytes", len(r_dl.content))

            r_nonce = self.session.get(
                f"{self.site_url}/wp-admin/plugin-install.php?tab=upload", timeout=15
            )
            up_nonce = re.search(r'name="_wpnonce" value="([^"]+)"', r_nonce.text)
            if not up_nonce:
                logger.error("WC: upload nonce not found")
                return False

            self.session.post(
                f"{self.site_url}/wp-admin/update.php?action=upload-plugin",
                files={"pluginzip": (f"{slug}.zip", io.BytesIO(r_dl.content), "application/zip")},
                data={"_wpnonce": up_nonce.group(1)},
                timeout=300, allow_redirects=True,
            )
            logger.info("WC uploaded")

        # Activate
        r_pl2 = self.session.get(f"{self.site_url}/wp-admin/plugins.php", timeout=15)
        act_match = re.search(
            rf'action=activate[^"]*plugin={slug}[^"]*_wpnonce=([a-f0-9]+)',
            r_pl2.text,
        )
        if act_match:
            act_url = act_match.group(0).replace("&amp;", "&")
            self.session.get(
                f"{self.site_url}/wp-admin/plugins.php?{act_url}",
                timeout=30, allow_redirects=True,
            )
            logger.info("WC activated")
        else:
            # Check if it's already active
            if 'deactivate>woocommerce<' in r_pl2.text.lower():
                logger.info("WC appears already active")
            else:
                logger.warning("WC activate link not found, may fail")

        # Verify
        return self.is_woocommerce_active()

    def list_prebuilt_demos(self) -> list[dict]:
        """Parse wp-admin/admin.php?page=xts_prebuilt_websites HTML to extract
        available demo versions with categories, titles, and thumbnails.
        """
        if not self._logged_in and not self.login():
            return []
        try:
            import re

            # Activate woodmart-core if needed (required for demo import page)
            r_plugs = self.session.get(
                f"{self.site_url}/wp-admin/plugins.php",
                timeout=self.timeout,
            )
            if re.search(r'action=activate[^"]*plugin=woodmart-core', r_plugs.text):
                nm = re.search(
                    r'action=activate[^"]*plugin=woodmart-core[^"]*_wpnonce=([a-f0-9]+)',
                    r_plugs.text,
                )
                if nm:
                    act_url = nm.group(0).replace("&amp;", "&")
                    logger.info("Activating woodmart-core before demo import: %s", act_url[:80])
                    self.session.get(
                        f"{self.site_url}/wp-admin/plugins.php?{act_url}",
                        timeout=self.timeout, allow_redirects=True,
                    )
                    if self.login():
                        logger.info("Re-logged in after woodmart-core activation")
                    else:
                        logger.warning("Login failed after woodmart-core activation")

            r = self.session.get(
                f"{self.site_url}/wp-admin/admin.php?page=xts_prebuilt_websites",
                timeout=self.timeout,
            )

            demos = []
            for m in re.finditer(
                r'<div class="xts-import-item[^"]*"\s+data-version="([^"]+)"\s+data-base="([^"]+)"\s+data-type="([^"]+)"(?:\s+data-tags="([^"]*)")?\s+data-cats="([^"]*)"',
                r.text,
            ):
                slug = m.group(1)
                base = m.group(2)
                dtype = m.group(3)
                tags = m.group(4) or ""
                cats_raw = m.group(5) or ""

                pos = m.end()
                chunk = r.text[pos:pos + 3000]
                img_m = re.search(r'<img[^>]*data-src="([^"]+)"', chunk)
                preview = img_m.group(1) if img_m else ""
                title_m = re.search(
                    r'<span class="xts-import-item-title">\s*([^<]+)\s*</span>',
                    chunk,
                )
                title = title_m.group(1).strip() if title_m else slug.replace("-", " ").title()

                cat_names = []
                for cat_slug in cats_raw.split(","):
                    cat_slug = cat_slug.strip()
                    if not cat_slug:
                        continue
                    cat_name_pat = rf'<li data-cat="{re.escape(cat_slug)}">\s*<a>\s*<span>([^<]+)</span>'
                    cat_name_m = re.search(cat_name_pat, r.text)
                    cat_name = cat_name_m.group(1) if cat_name_m else cat_slug
                    cat_names.append(cat_name)

                primary_cat = cat_names[0] if cat_names else ""

                demos.append({
                    "id": slug,
                    "name": title,
                    "category": primary_cat,
                    "categories": cat_names,
                    "type": dtype,
                    "base": base,
                    "tags": tags,
                    "thumbnail": preview,
                })

            logger.info("Parsed %d WoodMart demos from HTML", len(demos))
            return demos
        except Exception as e:
            logger.warning("list_prebuilt_demos error: %s", e)
            return []

    def import_prebuilt_demo(self, demo_id: str) -> dict:
        """Trigger WoodMart demo import via admin-ajax.php.

        WoodMart imports in SEQUENTIAL stages via 6 AJAX calls:
        ['xml', 'images1', 'images2', 'images3', 'images4', 'other']
        Each call returns JSON; 'other' returns preview_url + remove_html.
        Version demos import base first, then version.
        """
        if not self._logged_in and not self.login():
            return {"success": False, "message": "WordPress 登录失败"}

        if not self.ensure_woocommerce():
            return {"success": False, "message": "WooCommerce 安装/激活失败，无法导入演示"}

        try:
            import re
            import json as _json
            import time

            r = self.session.get(
                f"{self.site_url}/wp-admin/admin.php?page=xts_prebuilt_websites",
                timeout=self.timeout,
            )

            nonce = None
            for pat in [
                r'import_nonce\s*=\s*"([^"]+)"',
                r'import_nonce["\s:]+"([^"]+)"',
                r'"import_nonce"\s*:\s*"([^"]+)"',
            ]:
                m = re.search(pat, r.text)
                if m:
                    nonce = m.group(1)
                    break
            if not nonce:
                m = re.search(r'name="_wpnonce"\s+value="([^"]+)"', r.text)
                if m:
                    nonce = m.group(1)
            if not nonce:
                return {"success": False, "message": "无法获取 nonce"}

            base_m = re.search(
                rf'data-version="{re.escape(demo_id)}"[^>]*data-base="([^"]+)"',
                r.text,
            )
            base_slug = base_m.group(1) if base_m and base_m.group(1) != demo_id else None

            stages = ["xml", "images1", "images2", "images3", "images4", "other"]

            def _run_stages(version, imp_type, skip_images=False):
                for stage in stages:
                    if skip_images and "images" in stage:
                        continue
                    params = {
                        "action": "woodmart_import_action",
                        "version": version,
                        "type": imp_type,
                        "process": stage,
                        "security": nonce,
                    }
                    logger.info("Import stage: version=%s type=%s process=%s", version, imp_type, stage)
                    r2 = self.session.get(
                        f"{self.site_url}/wp-admin/admin-ajax.php",
                        params=params,
                        timeout=1800,
                    )
                    txt = r2.text.strip()
                    if r2.status_code != 200:
                        php_error = ""
                        fatal_header = r2.headers.get("X-PHP-Fatal", "")
                        if fatal_header:
                            php_error = fatal_header
                            logger.error("PHP Fatal (header): %s", fatal_header[:1000])
                        if not php_error:
                            try:
                                dbg_url = f"{self.site_url}/kairui-dbg.php?s=kairui_import_2024"
                                rd = self.session.get(dbg_url, timeout=10)
                                if rd.status_code == 200 and rd.text.strip():
                                    php_error = rd.text.strip()
                                    logger.error("PHP debug log: %s", php_error[:3000])
                            except Exception as ex:
                                logger.warning("Diag: kairui-dbg.php failed: %s", ex)
                        logger.error("Stage %s HTTP %s — body: %s", stage, r2.status_code, txt[:3000])
                        msg = f"stage {stage} HTTP {r2.status_code}"
                        if php_error:
                            msg += f" — PHP: {php_error[:500]}"
                        else:
                            msg += f": {txt[:500]}"
                        return {"success": False, "message": msg}
                    try:
                        result = _json.loads(txt)
                    except Exception:
                        logger.error("Stage %s JSON parse failed — body: %s", stage, txt[:3000])
                        return {"success": False, "message": f"stage {stage}: {txt[:500]}"}
                    if not result.get("success"):
                        err_detail = result.get('data', {}).get('errorMessage', '') or txt[:500]
                        logger.error("Stage %s failed — result: %s", stage, _json.dumps(result, ensure_ascii=False)[:3000])
                        return {"success": False, "message": f"stage {stage} failed: {err_detail}"}
                    logger.info("Stage %s OK", stage)
                    if stage == "other":
                        logger.info("Preview: %s", result.get("preview_url", ""))
                return {"success": True, "message": "import complete"}

            if base_slug:
                logger.info("Importing base: %s", base_slug)
                base_result = _run_stages(base_slug, "base")
                if not base_result["success"]:
                    return base_result
                time.sleep(2)

            result = _run_stages(demo_id, "version", skip_images=bool(base_slug))
            return result
        except Exception as e:
            logger.warning("import_prebuilt_demo error: %s", e)
            return {"success": False, "message": str(e)[:200]}

    def _ensure_wc_auth(self) -> bool:
        """Ensure the session can access WooCommerce REST API.

        If cookie auth doesn't work (WC sometimes blocks it), try generating
        an application password / consumer key for the current admin user.
        """
        if not self._logged_in and not self.login():
            return False
        # Quick check: try the system_status endpoint
        try:
            r = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/system_status",
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return True
            # WC API is returning non-200 — try to enable legacy API access
            # or fall back to scraping the WC settings page
            logger.warning("WC API returned %s, falling back to admin page scraping", r.status_code)
        except Exception as e:
            logger.warning("WC API auth check error: %s", e)
        return False

    def get_woocommerce_settings(self, group: str = "general") -> list:
        """Get WooCommerce settings for *group*: general/products/tax/shipping/payments etc."""
        if not self._logged_in and not self.login():
            return []
        try:
            r = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/settings/{group}",
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return r.json()
            # Fallback: scrape WooCommerce settings page
            logger.warning("WC REST API returned %s, trying admin page scrape", r.status_code)
            return self._scrape_wc_settings(group)
        except Exception as e:
            logger.warning("get_woocommerce_settings error: %s", e)
            return []

    def _scrape_wc_settings(self, group: str) -> list:
        """Scrape WooCommerce settings from admin page as fallback."""
        tab_map = {
            "general": "general", "products": "products", "shipping": "shipping",
            "payments": "checkout", "tax": "tax",
        }
        tab = tab_map.get(group, group)
        try:
            r = self.session.get(
                f"{self.site_url}/wp-admin/admin.php?page=wc-settings&tab={tab}",
                timeout=self.timeout,
            )
            import re
            items = []
            # Parse form inputs: <input name="woocommerce_xxx" value="yyy" ...>
            for m in re.finditer(
                r'name="(woocommerce_[^"]+)"[^>]*value="([^"]*)"',
                r.text,
            ):
                items.append({
                    "id": m.group(1),
                    "label": m.group(1).replace("woocommerce_", "").replace("_", " ").title(),
                    "value": m.group(2),
                    "type": "text",
                })
            # Parse select elements
            for m in re.finditer(
                r'<select[^>]*name="(woocommerce_[^"]+)"[^>]*>(.*?)</select>',
                r.text, re.DOTALL,
            ):
                sel_id = m.group(1)
                options = {}
                selected = ""
                for om in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*(selected)?[^>]*>([^<]+)</option>', m.group(2)):
                    options[om.group(1)] = om.group(3).strip()
                    if om.group(2):
                        selected = om.group(1)
                items.append({
                    "id": sel_id, "label": sel_id.replace("woocommerce_", "").replace("_", " ").title(),
                    "value": selected, "type": "select", "options": options,
                })
            # Parse checkboxes
            for m in re.finditer(
                r'name="(woocommerce_[^"]+)"[^>]*type="checkbox"[^>]*value="1"[^>]*(checked)?',
                r.text,
            ):
                items.append({
                    "id": m.group(1),
                    "label": m.group(1).replace("woocommerce_", "").replace("_", " ").title(),
                    "value": "yes" if m.group(2) else "no",
                    "type": "checkbox",
                })
            logger.info("Scraped %d WC settings from admin page for %s", len(items), group)
            return items
        except Exception as e:
            logger.warning("_scrape_wc_settings error: %s", e)
            return []

    def update_woocommerce_settings(self, group: str, data: dict) -> bool:
        """Update WooCommerce settings for *group*.

        Uses WC REST API with cookie auth + REST nonce.
        Falls back to WP admin AJAX/wp-admin POST if REST fails.
        """
        if not self._logged_in and not self.login():
            return False
        try:
            headers = {}
            rest_nonce = self._get_rest_nonce()
            if rest_nonce:
                headers["X-WP-Nonce"] = rest_nonce
            r = self.session.post(
                f"{self.site_url}/wp-json/wc/v3/settings/{group}/batch",
                json={"update": [{"id": k, "value": v} for k, v in data.items()]},
                headers=headers,
                timeout=self.timeout,
            )
            if r.status_code in (200, 201):
                # Check batch response for individual setting errors
                resp_data = r.json() if r.text else {}
                errors = []
                if isinstance(resp_data, dict) and "update" in resp_data:
                    for item in resp_data["update"]:
                        if item.get("error"):
                            errors.append(f"{item['id']}: {item['error']}")
                if not errors:
                    logger.info("WC settings [%s] updated: %s", group, list(data.keys()))
                    return True
                # Some settings failed — try failed ones via admin POST
                logger.warning("WC settings batch errors: %s", errors)
                failed = {item["id"]: data[item["id"]] for item in resp_data["update"] if item.get("error") and item["id"] in data}
                if failed:
                    return self._update_woo_via_admin(group, failed)
                return True
            logger.warning("WC settings update failed: status=%s resp=%s", r.status_code, r.text[:300])
            # Fallback: try wp-admin/admin-post.php with WooCommerce save action
            return self._update_woo_via_admin(group, data)
        except Exception as e:
            logger.warning("update_woocommerce_settings error: %s", e)
            return False

    def _update_woo_via_admin(self, group: str, data: dict) -> bool:
        """Fallback: update WooCommerce settings via wp-admin POST (cookie auth)."""
        try:
            # Get the WC settings page for the nonce
            r = self.session.get(
                f"{self.site_url}/wp-admin/admin.php?page=wc-settings&tab=general",
                timeout=self.timeout,
            )
            nonce_match = re.search(
                r'woocommerce-settings[^"]*_wpnonce["\']\s*value=["\']([^"\']+)',
                r.text,
            )
            nonce = nonce_match.group(1) if nonce_match else None

            # Build form data matching WC settings save
            form_data = {}
            for k, v in data.items():
                form_data[k] = str(v)
            form_data["save"] = "Save changes"
            if nonce:
                form_data["_wpnonce"] = nonce

            r2 = self.session.post(
                f"{self.site_url}/wp-admin/admin.php?page=wc-settings&tab=general",
                data=form_data,
                timeout=self.timeout,
                allow_redirects=True,
            )
            if r2.status_code == 200:
                logger.info("WC settings [%s] updated via admin POST: %s", group, list(data.keys()))
                return True
            logger.warning("WC admin POST fallback failed: status=%s", r2.status_code)
            return False
        except Exception as e:
            logger.warning("_update_woo_via_admin error: %s", e)
            return False

    # ------------------------------------------------------------------
    # Fallback: scrape/POST options-general.php
    # ------------------------------------------------------------------

    def _scrape_options_page(self) -> dict:
        """Scrape the WordPress options-general.php page for current values."""
        try:
            r = self.session.get(
                f"{self.site_url}/wp-admin/options-general.php",
                timeout=self.timeout,
            )
            import re
            result = {}
            # Match <input name="xxx" value="yyy" ...>
            for m in re.finditer(
                r'name="([^"]+)"[^>]*value="([^"]*)"',
                r.text,
            ):
                name = m.group(1)
                value = m.group(2)
                if name not in ("submit", "_wpnonce", "_wp_http_referer", "option_page", "action"):
                    result[name] = value
            # Also get the tagline from <textarea>
            tag_match = re.search(
                r'<textarea[^>]*name="blogdescription"[^>]*>(.*?)</textarea>',
                r.text, re.DOTALL,
            )
            if tag_match:
                result["blogdescription"] = tag_match.group(1)
            return result
        except Exception as e:
            logger.warning("_scrape_options_page error: %s", e)
            return {}

    def _post_options_page(self, data: dict) -> bool:
        """POST to options-general.php to save settings.

        IMPORTANT: WordPress options.php updates ALL whitelisted general
        options on every POST. Options not in the POST data get reset to
        empty/null. To avoid this, we scrape the current values from the
        page first, then override with the requested changes.
        """
        try:
            r = self.session.get(
                f"{self.site_url}/wp-admin/options-general.php",
                timeout=self.timeout,
            )
            import re
            nonce_match = re.search(
                r'name="_wpnonce" value="([^"]+)"', r.text
            )
            if not nonce_match:
                logger.warning("_post_options_page: nonce not found in options-general.php")
                return False

            # Build payload from ALL current values to prevent WordPress
            # from resetting options not included in the POST.
            # site_icon is NOT on options-general.php, so fetch it via REST.
            current = self._scrape_options_page()
            site_icon = current.get("site_icon")
            if site_icon is None:
                site_icon = self._get_setting("site_icon") or 0
            payload = {
                "_wpnonce": nonce_match.group(1),
                "_wp_http_referer": "/wp-admin/options-general.php",
                "option_page": "general",
                "action": "update",
                "blogname": current.get("blogname", ""),
                "blogdescription": current.get("blogdescription", ""),
                "admin_email": current.get("admin_email", ""),
                "timezone_string": current.get("timezone_string", ""),
                "date_format": current.get("date_format", "F j, Y"),
                "time_format": current.get("time_format", "g:i a"),
                "start_of_week": current.get("start_of_week", "1"),
                "WPLANG": current.get("WPLANG", ""),
                "siteurl": current.get("siteurl", ""),
                "home": current.get("home", ""),
                "default_role": current.get("default_role", "subscriber"),
                "users_can_register": current.get("users_can_register", "0"),
                "site_icon": site_icon,
            }
            # Override with requested changes
            field_map = {
                "title": "blogname",
                "description": "blogdescription",
                "admin_email": "admin_email",
                "timezone": "timezone_string",
                "date_format": "date_format",
                "time_format": "time_format",
                "week_starts_on": "start_of_week",
                "language": "WPLANG",
                "site_url": "siteurl",
                "home": "home",
                "default_role": "default_role",
                "users_can_register": "users_can_register",
                "site_icon": "site_icon",
            }
            for our_key, wp_key in field_map.items():
                if our_key in data:
                    payload[wp_key] = data[our_key]
                elif wp_key in data:
                    payload[wp_key] = data[wp_key]

            # Pass through any custom option keys not in field_map
            for key, value in data.items():
                if key not in field_map and key not in field_map.values():
                    payload[key] = value

            payload["submit"] = "Save Changes"
            logger.info("_post_options_page posting: %s", {k: v for k, v in payload.items() if k != "_wpnonce"})

            r2 = self.session.post(
                f"{self.site_url}/wp-admin/options.php",
                data=payload,
                timeout=self.timeout,
                allow_redirects=True,
            )
            if r2.status_code != 200:
                logger.warning("_post_options_page: options.php returned %s", r2.status_code)
            return r2.status_code == 200
        except Exception as e:
            logger.warning("_post_options_page error: %s", e)
            return False

    # ------------------------------------------------------------------
    # WoodMart Header Builder — probe & update
    # ------------------------------------------------------------------

    def _probe_header_builder(self) -> dict:
        """Visit xts_header_builder page and extract JS config to understand
        how headers are listed, loaded, and saved.

        Returns dict with:
          headers: list of {id, title, edit_url}
          ajax_action: the admin-ajax action name for saving
          nonce: security nonce for AJAX calls
          rest_base: REST API base if available
        """
        logger.info("_probe_header_builder: probing %s", self.site_url)
        if not self._logged_in and not self.login():
            logger.warning("_probe_header_builder: not logged in")
            return {"headers": [], "ajax_action": "", "nonce": "", "rest_base": ""}
        try:
            r = self.session.get(
                f"{self.site_url}/wp-admin/admin.php?page=xts_header_builder",
                timeout=self.timeout,
            )
            logger.info("_probe_header_builder: page loaded HTTP=%s len=%d", r.status_code, len(r.text))
            text = r.text
            result = {"headers": [], "ajax_action": "", "nonce": "", "rest_base": ""}

            # 1. Extract nonce for AJAX calls
            for pat in [
                r'"security"\s*:\s*"([^"]+)"',
                r'security["\s:]+"([^"]+)"',
                r'name="_wpnonce"\s+value="([^"]+)"',
                r'"nonce"\s*:\s*"([^"]+)"',
                r'xtsBuilderConfig.*?"security":"([^"]+)"',
            ]:
                m = re.search(pat, text)
                if m:
                    result["nonce"] = m.group(1)
                    logger.info("_probe_header_builder: nonce found via pattern")
                    break
            if not result["nonce"]:
                logger.info("_probe_header_builder: no nonce found in page")

            # 2. Extract AJAX save action name
            for pat in [
                r'"save_action"\s*:\s*"([^"]+)"',
                r'"saveAction"\s*:\s*"([^"]+)"',
                r'"action"\s*:\s*"([^"]+_header[^"]*)"',
                r'"action"\s*:\s*"([^"]+_layout[^"]*)"',
            ]:
                m = re.search(pat, text)
                if m:
                    result["ajax_action"] = m.group(1)
                    logger.info("_probe_header_builder: ajax_action=%s", result["ajax_action"])
                    break
            if not result["ajax_action"]:
                logger.info("_probe_header_builder: no ajax_action found in page")

            # 3. Extract REST API base
            rest_m = re.search(r'"rest_url"\s*:\s*"([^"]+)"', text)
            if rest_m:
                result["rest_base"] = rest_m.group(1)
                logger.info("_probe_header_builder: rest_base from page=%s", result["rest_base"])
            else:
                # Try common XTS routes
                for route in [
                    "xts/v1/header-builder",
                    "xts/v1/layouts",
                    "woodmart/v1/header-builder",
                ]:
                    r2 = self.session.get(
                        f"{self.site_url}/wp-json/{route}",
                        timeout=10,
                    )
                    if r2.status_code == 200 and r2.text.strip() not in ("", "[]", "{}"):
                        result["rest_base"] = f"{self.site_url}/wp-json/{route}"
                        logger.info("_probe_header_builder: rest_base from probe=%s", result["rest_base"])
                        break
            if not result["rest_base"]:
                logger.info("_probe_header_builder: no REST base found")

            # 4. List header entries (woodmart_layout posts of type 'header')
            #    Try to extract from the page JS first
            entries_m = re.search(r'"headers"\s*:\s*(\[[^\]]*\])', text)
            if entries_m:
                try:
                    import json as _json
                    result["headers"] = _json.loads(entries_m.group(1))
                    logger.info("_probe_header_builder: headers from JS=%s", result["headers"])
                except Exception:
                    pass

            # 5. Extract header IDs from the page if list is in HTML
            if not result["headers"]:
                for m in re.finditer(
                    r'(?:post|data-id)=["\'](\d+)["\'].*?(?:title|data-name)=["\']([^"\']+)["\']',
                    text,
                ):
                    result["headers"].append({"id": int(m.group(1)), "title": m.group(2)})
                if result["headers"]:
                    logger.info("_probe_header_builder: headers from HTML=%s", result["headers"])

            logger.info(
                "_probe_header_builder: SUMMARY %d headers, ajax_action=%s, rest=%s, nonce=%s",
                len(result["headers"]), result["ajax_action"] or "(none)",
                result["rest_base"] or "(none)", "yes" if result["nonce"] else "no",
            )
            return result
        except Exception as e:
            logger.warning("_probe_header_builder: EXCEPTION %s", e)
            return {"headers": [], "ajax_action": "", "nonce": "", "rest_base": ""}

    def _load_header_data(self, header_id: int, ajax_action: str = "",
                          nonce: str = "", rest_base: str = "") -> dict:
        """Load a WoodMart header's builder structure via AJAX or REST API.

        Returns {"success": bool, "data": list|dict, "message": str}
        """
        logger.info("_load_header_data: header_id=%d rest=%s ajax=%s", header_id, rest_base or "(none)", ajax_action or "(none)")
        if not self._logged_in and not self.login():
            logger.warning("_load_header_data: login failed")
            return {"success": False, "data": None, "message": "登录失败"}

        # Approach 1: XTS REST API
        if rest_base:
            try:
                r = self.session.get(
                    f"{rest_base}/{header_id}",
                    timeout=15,
                    headers={"X-WP-Nonce": self._get_rest_nonce() or ""},
                )
                if r.status_code == 200:
                    j = r.json()
                    logger.info("Header %d loaded via REST API", header_id)
                    return {"success": True, "data": j, "message": "ok"}
                logger.info("REST API returned %s for header %d", r.status_code, header_id)
            except Exception as e:
                logger.info("REST API failed for header %d: %s", header_id, e)

        # Approach 2: Admin AJAX
        if ajax_action and nonce:
            try:
                r = self.session.post(
                    f"{self.site_url}/wp-admin/admin-ajax.php",
                    data={"action": ajax_action, "id": header_id, "security": nonce},
                    timeout=15,
                )
                if r.status_code == 200:
                    txt = r.text.strip()
                    try:
                        j = __import__("json").loads(txt)
                        if j.get("success"):
                            logger.info("Header %d loaded via AJAX", header_id)
                            return {"success": True, "data": j.get("data", j), "message": "ok"}
                    except Exception:
                        pass
            except Exception as e:
                logger.info("AJAX load failed for header %d: %s", header_id, e)

        # Approach 3: Direct post_content + post_meta
        try:
            post = self.session.get(
                f"{self.site_url}/wp-json/wp/v2/woodmart_layout/{header_id}",
                timeout=15,
            )
            if post.status_code == 200:
                pj = post.json()
                content = pj.get("content", {}).get("rendered", "")
                meta = pj.get("meta", {})
                data = None
                # Try post_content JSON first
                if content.strip():
                    try:
                        data = __import__("json").loads(content.strip())
                    except Exception:
                        pass
                # Also try WP REST to get raw post
                if data is None:
                    r2 = self.session.get(
                        f"{self.site_url}/wp-json/wp/v2/woodmart_layout/{header_id}?context=edit",
                        timeout=15,
                        headers={"X-WP-Nonce": self._get_rest_nonce() or ""},
                    )
                    if r2.status_code == 200:
                        raw = r2.json()
                        raw_content = raw.get("content", {}).get("raw", "")
                        if raw_content.strip():
                            try:
                                data = __import__("json").loads(raw_content.strip())
                            except Exception:
                                pass
                if data:
                    logger.info("Header %d loaded via WP REST (post_content)", header_id)
                    return {"success": True, "data": data, "message": "ok"}
        except Exception as e:
            logger.info("WP REST fallback failed for header %d: %s", header_id, e)

        logger.warning("_load_header_data: ALL approaches failed for header %d", header_id)
        return {"success": False, "data": None, "message": "无法加载 header 数据"}

    def _save_header_data(self, header_id: int, data, ajax_action: str = "",
                          nonce: str = "", rest_base: str = "") -> bool:
        """Save updated WoodMart header builder data via AJAX or REST API."""
        logger.info("_save_header_data: header_id=%d rest=%s ajax=%s", header_id, rest_base or "(none)", ajax_action or "(none)")
        if not self._logged_in and not self.login():
            logger.warning("_save_header_data: login failed")
            return False

        import json as _json
        payload = _json.dumps(data) if not isinstance(data, str) else data

        # Approach 1: XTS REST API
        if rest_base:
            try:
                r = self.session.post(
                    f"{rest_base}/{header_id}",
                    json=data if not isinstance(data, str) else _json.loads(data),
                    timeout=15,
                    headers={
                        "Content-Type": "application/json",
                        "X-WP-Nonce": self._get_rest_nonce() or "",
                    },
                )
                if r.status_code in (200, 201):
                    logger.info("Header %d saved via REST API", header_id)
                    return True
                logger.info("REST save returned %s for header %d", r.status_code, header_id)
            except Exception as e:
                logger.info("REST save failed for header %d: %s", header_id, e)

        # Approach 2: Admin AJAX
        if ajax_action and nonce:
            try:
                r = self.session.post(
                    f"{self.site_url}/wp-admin/admin-ajax.php",
                    data={
                        "action": ajax_action,
                        "id": header_id,
                        "data": payload,
                        "security": nonce,
                    },
                    timeout=15,
                )
                if r.status_code == 200:
                    try:
                        j = _json.loads(r.text.strip())
                        if j.get("success"):
                            logger.info("Header %d saved via AJAX", header_id)
                            return True
                    except Exception:
                        pass
            except Exception as e:
                logger.info("AJAX save failed for header %d: %s", header_id, e)

        # Approach 3: Direct wp_update_post via REST API
        try:
            r = self.session.post(
                f"{self.site_url}/wp-json/wp/v2/woodmart_layout/{header_id}",
                json={"content": payload},
                timeout=15,
                headers={
                    "Content-Type": "application/json",
                    "X-WP-Nonce": self._get_rest_nonce() or "",
                },
            )
            if r.status_code in (200, 201):
                logger.info("Header %d saved via WP REST (post_content)", header_id)
                return True
        except Exception as e:
            logger.info("WP REST save failed for header %d: %s", header_id, e)

        logger.warning("_save_header_data: ALL approaches failed for header %d", header_id)
        return False

    def _update_logo_in_builder_data(self, data, attachment_id: int,
                                     logo_url: str) -> bool:
        """Recursively find all logo elements in builder data and replace their image.

        Returns True if any logo was updated.
        """
        updated = False

        def _walk(arr):
            nonlocal updated
            if not isinstance(arr, (dict, list)):
                return
            if isinstance(arr, list):
                for item in arr:
                    _walk(item)
                return
            # dict
            elem_type = arr.get("type", "")
            if elem_type and "logo" in str(elem_type).lower():
                # Common patterns: type='logo', type='wd_logo', type='xts-logo'
                params = arr.get("params", {}) or arr.get("content", {})
                if isinstance(params, dict) and "image" in params:
                    img = params["image"]
                    if isinstance(img, dict) and ("url" in img or "id" in img):
                        img["id"] = attachment_id
                        img["url"] = logo_url
                        updated = True
                elif isinstance(params, dict) and "url" in params:
                    params["url"] = logo_url
                    params["id"] = attachment_id
                    updated = True
            for v in arr.values():
                _walk(v)

        _walk(data)
        return updated

    def upload_site_icon(self, svg_content: str) -> dict:
        """Upload an SVG as site icon via wp-admin async-upload.php.

        Uses the wp-admin cookie-based upload which works reliably with the
        session login, unlike the REST API which requires Application Passwords
        for media create operations.

        Returns {"success": bool, "attachment_id": int|None, "message": str}.
        """
        if not self._logged_in and not self.login():
            return {"success": False, "attachment_id": None, "message": "WordPress 登录失败"}

        try:
            # Get upload nonce from media-new.php
            r = self.session.get(
                f"{self.site_url}/wp-admin/media-new.php",
                timeout=self.timeout,
            )
            nonce_match = re.search(r'name="_wpnonce"\s+value="([^"]+)"', r.text)
            if not nonce_match:
                return {"success": False, "attachment_id": None, "message": "无法获取上传nonce"}
            nonce = nonce_match.group(1)

            upload_headers = {"X-Requested-With": "XMLHttpRequest"}

            # Step 1: Try SVG upload via wp-admin async-upload.php
            r2 = self.session.post(
                f"{self.site_url}/wp-admin/async-upload.php",
                data={"_wpnonce": nonce, "post_id": "0", "name": "site-icon.svg"},
                files={"async-upload": ("site-icon.svg", svg_content.encode("utf-8"), "image/svg+xml")},
                headers=upload_headers,
                timeout=30,
                allow_redirects=False,
            )
            attachment_id = self._parse_async_upload_response(r2)
            if attachment_id:
                logger.info("SVG site icon uploaded via async-upload.php, attachment %s", attachment_id)
            else:
                logger.info("SVG upload via async-upload.php failed (%s) resp: %s", r2.status_code, r2.text[:500])

                # Refresh nonce (may have been consumed)
                r = self.session.get(
                    f"{self.site_url}/wp-admin/media-new.php",
                    timeout=self.timeout,
                )
                nonce_match = re.search(r'name="_wpnonce"\s+value="([^"]+)"', r.text)
                if not nonce_match:
                    return {"success": False, "attachment_id": None, "message": "无法获取上传nonce (PNG重试)"}
                nonce = nonce_match.group(1)

                png_bytes = self._make_minimal_png()
                r2 = self.session.post(
                    f"{self.site_url}/wp-admin/async-upload.php",
                    data={"_wpnonce": nonce, "post_id": "0", "name": "site-icon.png"},
                    files={"async-upload": ("site-icon.png", png_bytes, "image/png")},
                    headers=upload_headers,
                    timeout=30,
                    allow_redirects=False,
                )
                attachment_id = self._parse_async_upload_response(r2)
                if attachment_id:
                    logger.info("PNG fallback site icon uploaded, attachment %s", attachment_id)
                else:
                    logger.error("PNG async-upload also failed: %s resp: %s", r2.status_code, r2.text[:500])
                    return {"success": False, "attachment_id": None, "message": "图标上传失败"}

            # Step 2: Set site_icon option via REST API settings
            if self.update_settings({"site_icon": attachment_id}):
                logger.info("site_icon set to %s", attachment_id)
                return {"success": True, "attachment_id": attachment_id, "message": "站点图标已设置"}
            return {"success": False, "attachment_id": attachment_id, "message": "图标已上传但site_icon设置失败"}

        except Exception as e:
            logger.error("upload_site_icon error: %s", e)
            return {"success": False, "attachment_id": None, "message": str(e)[:200]}

    @staticmethod
    def _parse_async_upload_response(resp) -> int | None:
        """Parse async-upload.php response and return attachment_id or None.

        WordPress may return:
        - Plain JSON: {"success":true,"data":{"id":123,...}}
        - Textarea-wrapped: <textarea>{"success":true,...}</textarea>
        - Script-wrapped: <script>...{"success":true,...}...</script>
        - Redirect to media-new.php?posted=<id> (302)
        - Plain numeric attachment ID
        """
        try:
            data = _json.loads(resp.text)
            if data.get("success") and data.get("data", {}).get("id"):
                return data["data"]["id"]
        except Exception:
            pass
        # Try extracting JSON from <textarea> wrapper (plupload compat)
        m = re.search(r'<textarea[^>]*>\s*(\{.*?\})\s*</textarea>', resp.text, re.DOTALL)
        if m:
            try:
                data = _json.loads(m.group(1))
                if data.get("success") and data.get("data", {}).get("id"):
                    return data["data"]["id"]
            except Exception:
                pass
        # Try CDATA script wrapper
        m = re.search(r'<!\[CDATA\[.*?(\{"success":.+?\}).*?\]\]>', resp.text, re.DOTALL)
        if m:
            try:
                data = _json.loads(m.group(1))
                if data.get("success") and data.get("data", {}).get("id"):
                    return data["data"]["id"]
            except Exception:
                pass
        # Try extracting from redirect Location: media-new.php?posted=<id>
        if resp.status_code in (302, 301, 303, 307):
            loc = resp.headers.get("Location", "")
            m = re.search(r'posted=(\d+)', loc)
            if m:
                return int(m.group(1))
        # Try plain numeric response (attachment ID as raw number)
        text = resp.text.strip()
        if text.isdigit():
            return int(text)
        return None

    def upload_image(self, file_path: str) -> dict:
        """Upload an image file (PNG/JPG/ICO) to WordPress media library.

        Uses wp-admin async-upload.php cookie-based upload, same as upload_site_icon.
        Returns {"success": bool, "attachment_id": int|None, "message": str}.
        """
        logger.info("upload_image: START file=%s", file_path)
        if not self._logged_in and not self.login():
            logger.warning("upload_image: login failed")
            return {"success": False, "attachment_id": None, "message": "WordPress 登录失败"}
        if not os.path.isfile(file_path):
            logger.warning("upload_image: file not found %s", file_path)
            return {"success": False, "attachment_id": None, "message": f"文件不存在: {file_path}"}
        try:
            fname = os.path.basename(file_path)
            fsize = os.path.getsize(file_path)
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "png"
            mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                        "ico": "image/x-icon", "webp": "image/webp", "svg": "image/svg+xml"}
            mime = mime_map.get(ext, "image/png")
            logger.info("upload_image: reading %s (%d bytes, mime=%s)", fname, fsize, mime)
            with open(file_path, "rb") as fh:
                file_bytes = fh.read()

            r = self.session.get(f"{self.site_url}/wp-admin/media-new.php", timeout=self.timeout)
            nonce_match = re.search(r'name="_wpnonce"\s+value="([^"]+)"', r.text)
            if not nonce_match:
                logger.warning("upload_image: nonce not found in media-new.php")
                return {"success": False, "attachment_id": None, "message": "无法获取上传nonce"}
            nonce = nonce_match.group(1)
            logger.info("upload_image: got nonce=%s...", nonce[:8])

            r2 = self.session.post(
                f"{self.site_url}/wp-admin/async-upload.php",
                data={"_wpnonce": nonce, "post_id": "0", "name": fname},
                files={"async-upload": (fname, file_bytes, mime)},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=30,
                allow_redirects=False,
            )
            logger.info("upload_image: async-upload.php -> HTTP %s", r2.status_code)
            attachment_id = self._parse_async_upload_response(r2)
            if attachment_id:
                logger.info("upload_image: SUCCESS fname=%s attachment_id=%s", fname, attachment_id)
                return {"success": True, "attachment_id": attachment_id, "message": "图片已上传"}
            logger.warning("upload_image: FAILED fname=%s status=%s resp[:300]=%s", fname, r2.status_code, r2.text[:300])
            return {"success": False, "attachment_id": None, "message": "图片上传失败"}
        except Exception as e:
            logger.error("upload_image: EXCEPTION %s", e)
            return {"success": False, "attachment_id": None, "message": str(e)[:200]}

    def _set_site_logo_via_plugin(self, attachment_id: int) -> dict:
        """Fallback: set site logo via self-deleting PHP plugin.

        More robust than the Python API approach because it runs inside the
        WordPress PHP environment with full access to theme functions.
        Tries multiple strategies to find and update WoodMart header data.
        """
        import io, zipfile, time as _time
        logger.info("_set_site_logo_via_plugin: START attachment_id=%s site=%s", attachment_id, self.site_url)
        uid = str(int(_time.time() * 1000))[-8:]
        slug = f"ls-{uid}"
        main_file = f"{slug}/{slug}.php"

        php_code = f'''<?php
/**
 * Plugin Name: Logo Setter ({uid})
 * Version: 1.0
 */
$attachment_id = {attachment_id};
$logo_url = wp_get_attachment_url($attachment_id);
if (!$logo_url) $logo_url = '';

global $wpdb;
$updated = 0;
$diagnostics = array();
$diagnostics['attachment_id'] = $attachment_id;
$diagnostics['logo_url'] = $logo_url;

// ── 1. WP core ──
update_option('site_logo', $attachment_id);
set_theme_mod('custom_logo', $attachment_id);

// ── 2. WoodMart theme options (all possible keys) ──
$wd_opts = get_option('woodmart_options', array());
if (is_array($wd_opts)) {{
    $logo_keys = array('logo','logo_img','site_logo','main_logo','header_logo',
        'sticky_header_logo','sticky_logo','mobile_logo',
        'logo_retina','logo_sticky_retina','logo_mobile_retina');
    foreach ($logo_keys as $k) $wd_opts[$k] = $attachment_id;
    update_option('woodmart_options', $wd_opts);
    $diagnostics['wd_opts_updated'] = true;
}}

// ── 3. WoodMart header builder: data is in wp_options whb_{id}, NOT post_meta ──
// The header builder admin page is /wp-admin/admin.php?page=xts_header_builder
// Headers are stored as:  whb_main_header → header_id
//                         whb_saved_headers → array(id => name)
//                         whb_{id} → {{"name":"...", "settings":{{...}}, "structure":{{...}}}}
$header_ids = array();
$main_id = get_option('whb_main_header');
if ($main_id) $header_ids[] = $main_id;
$saved = get_option('whb_saved_headers');
if (is_array($saved)) {{
    foreach ($saved as $hid => $hname) {{
        if (!in_array($hid, $header_ids)) $header_ids[] = $hid;
    }}
}}
$diagnostics['header_ids'] = $header_ids;
$diagnostics['whb_headers_updated'] = 0;

foreach ($header_ids as $hid) {{
    $whb_data = get_option('whb_' . $hid);
    if (!is_array($whb_data) || empty($whb_data['structure'])) {{
        $diagnostics['whb_' . $hid] = empty($whb_data) ? 'empty' : 'no_structure';
        continue;
    }}
    $str_before = json_encode($whb_data['structure']);
    _ls_fix_logo($whb_data['structure'], $attachment_id, $logo_url);
    $str_after = json_encode($whb_data['structure']);

    if ($str_before !== $str_after) {{
        update_option('whb_' . $hid, $whb_data);
        $diagnostics['whb_headers_updated']++;
        $diagnostics['whb_' . $hid] = 'updated (name=' . ($whb_data['name'] ?? '') . ')';
        $updated++;
    }} else {{
        $diagnostics['whb_' . $hid] = 'no_change (name=' . ($whb_data['name'] ?? '') . ')';
    }}
}}

// ── 3b. Also try woodmart_layout posts (for shop/cart layouts, NOT headers) ──
$layouts = $wpdb->get_results(
    "SELECT ID, post_title, post_content, post_status FROM $wpdb->posts WHERE post_type = 'woodmart_layout'",
    ARRAY_A
);
$diagnostics['layout_count'] = count($layouts);
if (!empty($layouts)) {{
    foreach ($layouts as $layout) {{
        $lid = (int)$layout['ID'];
        $elem_data = get_post_meta($lid, '_elementor_data', true);
        if (empty($elem_data)) {{
            $diagnostics['layout_' . $lid] = 'no_elementor_data';
            continue;
        }}
        $data = @json_decode($elem_data, true);
        if (!is_array($data)) {{
            $diagnostics['layout_' . $lid] = 'json_decode_failed';
            continue;
        }}
        $before = json_encode($data);
        _ls_fix_logo($data, $attachment_id, $logo_url);
        $after = json_encode($data);
        if ($before !== $after) {{
            update_post_meta($lid, '_elementor_data', wp_slash(json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)));
            $updated++;
            $diagnostics['layout_' . $lid] = 'updated';
        }}
    }}
}}

$diagnostics['updated_layouts'] = $updated;

// ── 4. Clear WoodMart transients ──
$wpdb->query("DELETE FROM $wpdb->options WHERE option_name LIKE '%_transient_woodmart_%' OR option_name LIKE '%_transient_xts_%' OR option_name LIKE '%_transient_redux_%'");
$wpdb->query("DELETE FROM $wpdb->options WHERE option_name LIKE '%_transient_timeout_woodmart_%' OR option_name LIKE '%_transient_timeout_xts_%' OR option_name LIKE '%_transient_timeout_redux_%'");

// Save diagnostics for Python to read back
update_option('_kairui_logo_result', $diagnostics);

// Write diagnostics to a file Python can fetch
@file_put_contents(ABSPATH . 'wp-content/_kairui_logo_diag.json',
    json_encode($diagnostics, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));

// ── RECURSIVE LOGO UPDATER ──
function _ls_fix_logo(&$arr, $id, $url) {{
    if (!is_array($arr)) return;
    $is_logo = false;
    if (isset($arr['type']) && $arr['type'] === 'logo') $is_logo = true;
    if (isset($arr['widgetType']) && stripos($arr['widgetType'], 'logo') !== false) $is_logo = true;
    if (isset($arr['name']) && stripos($arr['name'], 'logo') !== false) $is_logo = true;

    if ($is_logo) {{
        // WoodMart header builder stores image value as URL STRING (not array)
        if (isset($arr['params']['image']['value'])) {{
            if (is_array($arr['params']['image']['value'])) {{
                $arr['params']['image']['value']['id'] = $id;
                $arr['params']['image']['value']['url'] = $url;
            }} else {{
                $arr['params']['image']['value'] = $url;
            }}
        }}
        if (isset($arr['params']['image_id'])) {{
            if (is_array($arr['params']['image_id'])) {{
                $arr['params']['image_id']['value'] = $id;
            }} else {{
                $arr['params']['image_id'] = $id;
            }}
        }}
        if (isset($arr['params']['sticky_image']['value'])) {{
            if (is_array($arr['params']['sticky_image']['value'])) {{
                $arr['params']['sticky_image']['value']['id'] = $id;
                $arr['params']['sticky_image']['value']['url'] = $url;
            }} else {{
                $arr['params']['sticky_image']['value'] = $url;
            }}
        }}
        if (isset($arr['content']['url'])) {{
            $arr['content']['url'] = $url;
            $arr['content']['id'] = $id;
        }}
        if (isset($arr['params']['url'])) {{
            $arr['params']['url'] = $url;
            $arr['params']['id'] = $id;
        }}
        // Elementor format: settings.image may be array or string
        if (isset($arr['settings']['image'])) {{
            $arr['settings']['image'] = is_array($arr['settings']['image'])
                ? array_merge($arr['settings']['image'], array('id' => $id, 'url' => $url))
                : $url;
        }}
    }}
    foreach ($arr as &$v) _ls_fix_logo($v, $id, $url);
}}

// Self-destruct
register_deactivation_hook(__FILE__, function() {{
    $files = @glob(__DIR__."/*"); if ($files) {{ foreach ($files as $f) @unlink($f); }}
    @rmdir(__DIR__);
}});
add_action("shutdown", function() {{ deactivate_plugins(plugin_basename(__FILE__)); }});
'''

        r = self.session.get(f"{self.site_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
        nonce_match = re.search(r'name="_wpnonce" value="([^"]+)"', r.text)
        if not nonce_match:
            logger.warning("_set_site_logo_via_plugin: upload nonce not found")
            return {"success": False, "message": "无法获取上传凭据"}

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(main_file, php_code)
        buf.seek(0)
        logger.info("_set_site_logo_via_plugin: uploading plugin zip slug=%s", slug)
        r2 = self.session.post(
            f"{self.site_url}/wp-admin/update.php?action=upload-plugin",
            data={"_wpnonce": nonce_match.group(1)},
            files={"pluginzip": (f"{slug}.zip", buf.read(), "application/zip")},
            timeout=30,
        )
        ok = r2.status_code == 200 and ("installed" in r2.text.lower() or "activate" in r2.text.lower())
        if not ok:
            logger.warning("Logo setter plugin upload failed: status=%s text[:200]=%s", r2.status_code, r2.text[:200])
            return {"success": False, "message": "Logo设置插件上传失败"}

        logger.info("_set_site_logo_via_plugin: plugin uploaded, looking for activate link...")
        activate_url_match = re.search(rf'action=activate[^"]*plugin={slug}[^"]*', r2.text)
        if activate_url_match:
            activate_href = activate_url_match.group(0).replace("&amp;", "&")
            r_act = self.session.get(f"{self.site_url}/wp-admin/plugins.php?{activate_href}", timeout=15)
            logger.info("_set_site_logo_via_plugin: plugin %s activated (HTTP %s)", slug, r_act.status_code)

            # Read back diagnostics written by the plugin to verify what happened
            diag = self._read_plugin_diag("_kairui_logo_diag.json")
            logger.info("_set_site_logo_via_plugin: diagnostics=%s", diag)
            if diag:
                layout_count = diag.get("layout_count", 0)
                updated = diag.get("updated_layouts", 0)
                if layout_count == 0:
                    return {"success": False, "message": f"未找到 WoodMart header 布局 (woodmart_layout 帖子数=0)"}
                if updated == 0:
                    return {"success": False, "message": f"找到 {layout_count} 个布局但未找到 logo 元素"}
                return {"success": True, "message": f"站点Logo已设置（更新了 {updated}/{layout_count} 个布局）"}
            return {"success": True, "message": "站点Logo已设置（插件模式）"}

        logger.warning("_set_site_logo_via_plugin: activate link not found for %s", slug)
        return {"success": False, "message": "Logo设置插件激活失败"}

    def set_site_logo(self, attachment_id: int) -> dict:
        """Set site logo via self-deleting PHP plugin.

        The plugin runs inside WordPress with full DB access. It:
        1. Queries woodmart_layout posts and updates logo elements
        2. Sets woodmart_options logo keys
        3. Sets WP core site_logo / custom_logo
        4. Clears WoodMart and Elementor caches
        5. Writes diagnostics to wp-content/_kairui_logo_diag.json
        """
        logger.info("set_site_logo: START attachment_id=%s site=%s", attachment_id, self.site_url)
        if not self._logged_in and not self.login():
            logger.warning("set_site_logo: login failed for %s", self.site_url)
            return {"success": False, "message": "WordPress 登录失败"}

        result = self._set_site_logo_via_plugin(attachment_id)
        logger.info("set_site_logo: result=%s", result)
        return result

    def save_footer_settings(self, address: str = "", phone: str = "",
                              email: str = "", logo_attachment_id: int = 0) -> bool:
        """Save footer settings to WoodMart theme via self-deleting plugin.

        Creates or updates a cms_block post with contact info HTML and assigns
        it as the WoodMart footer HTML block. Also saves wp_footer_* options
        for REST API access.
        """
        import io, zipfile, time as _time
        logger.info("save_footer_settings: START address=%s phone=%s email=%s logo_id=%s site=%s",
                    address[:30] if address else "(none)", phone, email, logo_attachment_id, self.site_url)
        if not self._logged_in and not self.login():
            logger.warning("save_footer_settings: login failed")
            return False
        try:
            uid = str(int(_time.time() * 1000))[-8:]
            slug = f"fs-{uid}"
            main_file = f"{slug}/{slug}.php"

            def _esc_php(s):
                return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

            php_code = f'''<?php
/**
 * Plugin Name: Footer Settings ({uid})
 * Description: Save footer to WoodMart cms_block + assign as footer
 * Version: 1.0
 */

global $wpdb;
$diagnostics = array();

// 1. Save wp_footer_* options (backward compat / REST API)
update_option('wp_footer_address', '{_esc_php(address)}');
update_option('wp_footer_phone', '{_esc_php(phone)}');
update_option('wp_footer_email', '{_esc_php(email)}');
update_option('wp_footer_logo', '{logo_attachment_id}');

// 2. Build footer HTML content
$footer_html = '';
if ({logo_attachment_id}) {{
    $logo_url = wp_get_attachment_url({logo_attachment_id});
    if ($logo_url) {{
        $footer_html .= '<div class="footer-logo"><img src="' . esc_url($logo_url) . '" alt="logo" style="max-width:180px;height:auto;"></div>';
    }}
}}
$footer_html .= '<div class="footer-contact" style="margin-top:12px;line-height:0.5;">';
if ('{_esc_php(address)}') $footer_html .= '<p>{_esc_php(address)}</p>';
if ('{_esc_php(phone)}') $footer_html .= '<p><a href="tel:{_esc_php(phone)}">{_esc_php(phone)}</a></p>';
if ('{_esc_php(email)}') $footer_html .= '<p><a href="mailto:{_esc_php(email)}">{_esc_php(email)}</a></p>';
$footer_html .= '</div>';

// 3. Find footer blocks: cms_block with "footer" in title
$blocks = $wpdb->get_results(
    $wpdb->prepare(
        "SELECT ID, post_title, post_content, post_type FROM $wpdb->posts WHERE post_type = 'cms_block' AND post_title LIKE %s",
        '%' . $wpdb->esc_like('footer') . '%'
    ),
    ARRAY_A
);
$diagnostics['cms_block_count'] = count($blocks ?: array());

// 4. If no cms_block found, try elementor_library footer templates
if (empty($blocks)) {{
    $el_blocks = $wpdb->get_results(
        "SELECT p.ID, p.post_title, p.post_content, p.post_type FROM $wpdb->posts p
         INNER JOIN $wpdb->postmeta pm ON p.ID = pm.post_id AND pm.meta_key = '_elementor_template_type'
         WHERE p.post_type = 'elementor_library' AND p.post_status = 'publish' AND pm.meta_value = 'footer'",
        ARRAY_A
    );
    if (!empty($el_blocks)) {{
        $blocks = $el_blocks;
        $diagnostics['elementor_footer_count'] = count($el_blocks);
    }}
}}

// 5. Check WoodMart options for footer_html_block reference
$wd_opts = get_option('woodmart_options', array());
$wd_footer_block = isset($wd_opts['footer_html_block']) ? (int)$wd_opts['footer_html_block'] : 0;
$diagnostics['woodmart_footer_block'] = $wd_footer_block;

if ($wd_footer_block) {{
    $found = false;
    if (!empty($blocks)) {{
        foreach ($blocks as $b) {{
            if ((int)$b['ID'] === $wd_footer_block) {{ $found = true; break; }}
        }}
    }}
    if (!$found) {{
        $ref_post = $wpdb->get_row($wpdb->prepare("SELECT ID, post_title, post_content, post_type FROM $wpdb->posts WHERE ID = %d", $wd_footer_block), ARRAY_A);
        if ($ref_post) {{
            if (empty($blocks)) $blocks = array();
            $blocks[] = $ref_post;
            $diagnostics['added_ref_block'] = $wd_footer_block;
        }}
    }}
}}

// 6. Update all found blocks
$updated_count = 0;
$block_id = 0;

if (empty($blocks)) {{
    $diagnostics['result'] = 'no_blocks_found';
}} else {{
    foreach ($blocks as $b) {{
        $bid = (int)$b['ID'];
        $btype = $b['post_type'];
        $btitle = $b['post_title'];

        // Check if this block is Elementor-managed
        $is_elementor = false;
        $elem_data = get_post_meta($bid, '_elementor_data', true);
        if (!empty($elem_data)) {{
            $is_elementor = true;
        }}

        if ($is_elementor) {{
            // Elementor block: inject footer HTML as a section before existing content
            $_did_elem = _inject_footer_elementor($bid, $footer_html);
            $diagnostics['block_' . $bid] = array(
                'type' => $btype,
                'title' => $btitle,
                'elementor' => true,
                'updated' => $_did_elem,
            );
            if ($_did_elem) {{
                $updated_count++;
                if ($block_id === 0) $block_id = $bid;
            }}
        }} else {{
            // Non-Elementor block: prepend HTML to post_content
            $new_content = $footer_html . "\n" . $b['post_content'];
            $wpdb->update($wpdb->posts, array('post_content' => $new_content), array('ID' => $bid));
            $updated_count++;
            if ($block_id === 0) $block_id = $bid;

            $diagnostics['block_' . $bid] = array(
                'type' => $btype,
                'title' => $btitle,
                'elementor' => false,
                'updated' => true,
            );
        }}
    }}
    $diagnostics['result'] = 'updated';
    $diagnostics['updated_count'] = $updated_count;
    $diagnostics['primary_block_id'] = $block_id;
}}

// 7. Assign block to WoodMart footer options
if ($block_id) {{
    if (!is_array($wd_opts)) $wd_opts = array();
    $wd_opts['footer_content_type'] = 'html_block';
    $wd_opts['footer_html_block'] = $block_id;
    $wd_opts['prefooter_area'] = $block_id;
    $wd_opts['footer_block'] = $block_id;
    $wd_opts['footer_text_color'] = 'dark';
    if (isset($wd_opts['footer-layout'])) unset($wd_opts['footer-layout']);
    update_option('woodmart_options', $wd_opts);
    set_theme_mod('footer_html_block', $block_id);
    set_theme_mod('footer_block', $block_id);
    $diagnostics['woodmart_opts_updated'] = true;
}}

// 8. Clear all caches
$wpdb->query("DELETE FROM $wpdb->options WHERE option_name LIKE '%_transient_woodmart_%' OR option_name LIKE '%_transient_xts_%' OR option_name LIKE '%_transient_redux_%'");
$wpdb->query("DELETE FROM $wpdb->options WHERE option_name LIKE '%_transient_timeout_woodmart_%' OR option_name LIKE '%_transient_timeout_xts_%' OR option_name LIKE '%_transient_timeout_redux_%'");
$wpdb->query("DELETE FROM $wpdb->options WHERE option_name LIKE 'elementor_css_%'");

// Save diagnostics for Python to read back
update_option('_kairui_footer_result', $diagnostics);

@file_put_contents(ABSPATH . 'wp-content/_kairui_footer_diag.json',
    json_encode($diagnostics, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));

// ── ELEMENTOR HELPER: inject footer HTML as an Elementor section ──
function _inject_footer_elementor($post_id, $html) {{
    $elem_data = get_post_meta($post_id, '_elementor_data', true);
    if (empty($elem_data)) return false;
    $data = @json_decode($elem_data, true);
    if (!is_array($data)) return false;

    // Find the last section, then its first column, and inject footer HTML there
    $last_section = &$data[count($data) - 1];
    if (!isset($last_section['elements']) || !is_array($last_section['elements']) || empty($last_section['elements'])) {{
        return false;
    }}
    $first_column = &$last_section['elements'][0];
    if (!isset($first_column['elements']) || !is_array($first_column['elements'])) {{
        $first_column['elements'] = array();
    }}

    // Replace the first column's elements with the footer content
    $uid = uniqid();
    $first_column['elements'] = array(
        array(
            'id' => $uid . 'w',
            'elType' => 'widget',
            'widgetType' => 'html',
            'settings' => array('html' => $html),
        ),
    );

    $new_json = json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    update_post_meta($post_id, '_elementor_data', wp_slash($new_json));
    // Also update post_content as fallback for theme rendering
    global $wpdb;
    $wpdb->update($wpdb->posts, array('post_content' => $html . "\n<!-- elementor footer injected -->"), array('ID' => $post_id));
    return true;
}}

// Self-destruct (footer)
register_deactivation_hook(__FILE__, function() {{
    $dir = __DIR__;
    $files = @glob($dir . "/*");
    if ($files) {{ foreach ($files as $f) @unlink($f); }}
    @rmdir($dir);
}});
add_action("shutdown", function() {{ deactivate_plugins(plugin_basename(__FILE__)); }});
'''

            # Get upload nonce — retry with re-login if session expired
            nonce_match = None
            for nonce_attempt in range(3):
                r = self.session.get(f"{self.site_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
                nonce_match = re.search(r'name="_wpnonce" value="([^"]+)"', r.text)
                if nonce_match:
                    break
                logger.warning("save_footer_settings: nonce attempt %d: status=%s text_len=%d", nonce_attempt+1, r.status_code, len(r.text))
                if nonce_attempt < 2:
                    self._logged_in = False
                    self.login()
                    _time.sleep(2)
            if not nonce_match:
                logger.warning("save_footer_settings: upload nonce not found after 3 attempts")
                return False

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(main_file, php_code)
            buf.seek(0)
            logger.info("save_footer_settings: uploading plugin zip slug=%s", slug)
            r2 = self.session.post(
                f"{self.site_url}/wp-admin/update.php?action=upload-plugin",
                data={"_wpnonce": nonce_match.group(1)},
                files={"pluginzip": (f"{slug}.zip", buf.read(), "application/zip")},
                timeout=30,
            )
            ok = r2.status_code == 200 and ("installed" in r2.text.lower() or "activate" in r2.text.lower())
            if not ok:
                logger.warning("save_footer_settings: plugin upload failed status=%s text[:200]=%s", r2.status_code, r2.text[:200])
                return False

            logger.info("save_footer_settings: plugin uploaded, looking for activate link...")
            activate_url_match = re.search(rf'action=activate[^"]*plugin={slug}[^"]*', r2.text)
            if activate_url_match:
                activate_href = activate_url_match.group(0).replace("&amp;", "&")
                r_act = self.session.get(f"{self.site_url}/wp-admin/plugins.php?{activate_href}", timeout=15)
                logger.info("save_footer_settings: plugin %s activated (HTTP %s)", slug, r_act.status_code)

                # Read back diagnostics written by the plugin
                diag = self._read_plugin_diag("_kairui_footer_diag.json")
                logger.info("save_footer_settings: diagnostics=%s", diag)
                if diag:
                    result = diag.get("result", "unknown")
                    cms_count = diag.get("cms_block_count", 0)
                    elem_count = diag.get("elementor_footer_count", 0)
                    updated = diag.get("updated_count", 0)
                    if result == "no_blocks_found":
                        return False
                    if updated == 0 and result != "updated":
                        logger.warning("save_footer_settings: unexpected result=%s", result)
                    logger.info("save_footer_settings: SUCCESS — updated %d blocks (cms=%d elementor_footer=%d)", updated, cms_count, elem_count)
                    return True
                logger.info("save_footer_settings: SUCCESS (no diagnostics)")
                return True
            else:
                logger.warning("save_footer_settings: activate link not found for %s", slug)
                return False
        except Exception as e:
            logger.error("save_footer_settings: EXCEPTION %s", e)
            return False

    # ------------------------------------------------------------------
    # Google Shopping Feed
    # ------------------------------------------------------------------

    def create_sample_products(self) -> bool:
        """Create 4 sample WooCommerce products for a new store via REST API.

        Returns True if all products were created successfully.
        """
        if not self._logged_in and not self.login():
            return False
        try:
            rest_nonce = self._get_rest_nonce()
            headers = {}
            if rest_nonce:
                headers["X-WP-Nonce"] = rest_nonce

            samples = [
                {"name": "经典款T恤", "type": "simple", "regular_price": "29.99",
                 "description": "高品质纯棉经典款T恤，舒适透气，适合日常穿着。多种颜色可选。",
                 "short_description": "经典百搭T恤，纯棉面料"},
                {"name": "时尚运动鞋", "type": "simple", "regular_price": "89.99",
                 "description": "轻便舒适的运动鞋，采用透气网面设计，适合跑步和日常运动。",
                 "short_description": "轻便透气运动鞋"},
                {"name": "商务双肩包", "type": "simple", "regular_price": "59.99",
                 "description": "大容量商务双肩包，防水面料，多隔层设计，可容纳15.6寸笔记本。",
                 "short_description": "防水商务双肩包，大容量"},
                {"name": "无线蓝牙耳机", "type": "simple", "regular_price": "49.99",
                 "description": "高品质无线蓝牙耳机，主动降噪，续航8小时，支持快速充电。",
                 "short_description": "降噪无线蓝牙耳机"},
            ]

            created = 0
            for prod in samples:
                r = self.session.post(
                    f"{self.site_url}/wp-json/wc/v3/products",
                    json={"status": "publish", "stock_status": "instock", **prod},
                    headers=headers, timeout=30,
                )
                if r.status_code in (200, 201):
                    created += 1
                else:
                    logger.warning("create_sample_products: %s failed HTTP %s", prod["name"], r.status_code)

            logger.info("create_sample_products: %d/%d created", created, len(samples))
            return created > 0
        except Exception as e:
            logger.warning("create_sample_products error: %s", e)
            return False

    def generate_google_feed(self) -> dict:
        """Generate Google Shopping product feed XML and upload to wp-content.

        Pulls all published products via WooCommerce REST API and writes a
        standards-compliant Google Shopping feed file to the site.
        Auto-creates sample products when the store has none.
        """
        import xml.etree.ElementTree as ET
        import gzip

        if not self._logged_in and not self.login():
            return {"success": False, "message": "登录失败", "products": 0, "size_bytes": 0}

        rest_nonce = self._get_rest_nonce()
        headers = {}
        if rest_nonce:
            headers["X-WP-Nonce"] = rest_nonce

        products = []
        page = 1
        per_page = 100
        while True:
            try:
                r = self.session.get(
                    f"{self.site_url}/wp-json/wc/v3/products",
                    params={"per_page": per_page, "page": page, "status": "publish"},
                    headers=headers, timeout=60,
                )
                if r.status_code != 200:
                    logger.warning("generate_google_feed: product fetch HTTP %s: %s", r.status_code, r.text[:200])
                    break
                batch = r.json()
                if isinstance(batch, list):
                    products.extend(batch)
                else:
                    logger.warning("generate_google_feed: non-list response: %s", str(batch)[:200])
                    break
                if len(batch) < per_page:
                    break
                page += 1
            except Exception as e:
                logger.warning("generate_google_feed: product fetch error page=%s: %s", page, e)
                break

        if not products:
            # Auto-create sample products for new stores
            logger.info("generate_google_feed: no products found, creating samples...")
            ok = self.create_sample_products()
            if not ok:
                return {"success": False, "message": "没有已发布的产品，且自动创建失败", "products": 0, "size_bytes": 0}
            # Retry fetching products after creation
            products = []
            page = 1
            while True:
                try:
                    r = self.session.get(
                        f"{self.site_url}/wp-json/wc/v3/products",
                        params={"per_page": per_page, "page": page, "status": "publish"},
                        headers=headers, timeout=60,
                    )
                    if r.status_code != 200:
                        logger.warning("generate_google_feed: retry fetch HTTP %s: %s", r.status_code, r.text[:200])
                        break
                    batch = r.json()
                    if isinstance(batch, list):
                        products.extend(batch)
                    else:
                        break
                    if len(batch) < per_page:
                        break
                    page += 1
                except Exception as e:
                    logger.warning("generate_google_feed: product fetch retry error page=%s: %s", page, e)
                    break
            if not products:
                return {"success": False, "message": "已创建示例产品但未获取到", "products": 0, "size_bytes": 0}

        # Build RSS 2.0 + Google Shopping XML
        ns_g = "http://base.google.com/ns/1.0"
        rss = ET.Element("rss", {"version": "2.0", "xmlns:g": ns_g})
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = self.site_url
        ET.SubElement(channel, "link").text = self.site_url
        ET.SubElement(channel, "description").text = "Google Shopping Product Feed"

        site_url_clean = self.site_url.rstrip("/")

        for p in products:
            item = ET.SubElement(channel, "item")

            pid = str(p.get("id", ""))
            ET.SubElement(item, "g:id").text = pid
            ET.SubElement(item, "g:title").text = (p.get("name") or "")[:150]
            ET.SubElement(item, "g:description").text = self._strip_html(p.get("description") or p.get("short_description") or "")[:5000]

            # Link
            permalink = p.get("permalink") or f"{site_url_clean}/?p={p.get('id')}"
            ET.SubElement(item, "g:link").text = permalink

            # Image
            images = p.get("images") or []
            if images:
                ET.SubElement(item, "g:image_link").text = images[0].get("src", "")
                # Additional images
                for img in images[1:11]:  # Google allows up to 10
                    ET.SubElement(item, "g:additional_image_link").text = img.get("src", "")

            # Price
            price = str(p.get("price") or "")
            regular_price = str(p.get("regular_price") or "")
            sale_price = str(p.get("sale_price") or "")
            if sale_price and regular_price:
                ET.SubElement(item, "g:price").text = f"{regular_price} {p.get('currency', p.get('currency_symbol', 'USD'))}"
                ET.SubElement(item, "g:sale_price").text = f"{sale_price} {p.get('currency', p.get('currency_symbol', 'USD'))}"
            elif price:
                ET.SubElement(item, "g:price").text = f"{price} {p.get('currency', p.get('currency_symbol', 'USD'))}"

            # Availability
            stock_status = p.get("stock_status", "instock")
            avail = "in_stock" if stock_status == "instock" else "out_of_stock" if stock_status == "outofstock" else "preorder"
            if p.get("backorders") == "yes":
                avail = "preorder"
            ET.SubElement(item, "g:availability").text = avail

            # Condition
            ET.SubElement(item, "g:condition").text = "new"

            # Brand: try product attributes first, then fallback to site name
            brand = ""
            for attr in (p.get("attributes") or []):
                if (attr.get("name") or "").lower() in ("brand", "品牌"):
                    brand = (attr.get("options") or [""])[0] if attr.get("options") else ""
            if not brand:
                try:
                    r_opts = self.session.get(f"{self.site_url}/wp-json/wp/v2/settings", timeout=10)
                    if r_opts.status_code == 200:
                        brand = r_opts.json().get("title", "")
                except Exception:
                    pass
            if brand:
                ET.SubElement(item, "g:brand").text = brand[:70]

            # GTIN / MPN from SKU or meta
            sku = p.get("sku", "")
            if sku:
                ET.SubElement(item, "g:mpn").text = sku[:70]

            # Product category
            categories = p.get("categories") or []
            if categories:
                cat_names = " > ".join([c.get("name", "") for c in categories])
                ET.SubElement(item, "g:product_type").text = cat_names[:750]

            # Shipping weight
            weight = p.get("weight")
            if weight:
                ET.SubElement(item, "g:shipping_weight").text = f"{weight} {p.get('dimensions', {}).get('unit', 'kg')}"

        import base64 as _b64
        xml_str = ET.tostring(rss, encoding="unicode")
        xml_b64 = _b64.b64encode(xml_str.encode("utf-8")).decode("ascii")

        # Upload via self-deleting PHP (writes feed XML + diag JSON, then self-destructs).
        # The PHP code is fully standalone — no WordPress functions required.
        # Paths are resolved relative to __FILE__:
        #   __FILE__      = wp-content/plugins/kairui_feed_gen/kairui_feed_gen.php
        #   dirname(…, 2) = wp-content/plugins
        #   dirname(…, 3) = wp-content           ← target base dir
        diag_file = "kairui_feed_diag.json"
        php_code = f'''<?php
/**
 * Plugin Name: Kairui Feed Generator
 * Description: Writes Google Shopping feed XML and self-deletes
 */
error_reporting(E_ALL); ini_set('display_errors', 0);
try {{
    $base = dirname(__FILE__, 3);
    $uploads = $base . '/uploads';
    if (!is_dir($uploads)) mkdir($uploads, 0755, true);
    $feed = $uploads . '/google-feed.xml';
    $diag = $base . '/{diag_file}';
    $xml = base64_decode('{xml_b64}');
    if ($xml === false) {{
        $r = ["ok" => false, "error" => "base64_decode_failed"];
    }} elseif (file_put_contents($feed, $xml)) {{
        $r = ["ok" => true, "size" => filesize($feed)];
    }} else {{
        $r = ["ok" => false, "error" => "write_failed"];
    }}
}} catch (Throwable $e) {{
    $r = ["ok" => false, "error" => "php_error: " . $e->getMessage()];
}}
file_put_contents($diag, json_encode($r));
echo json_encode($r);
unlink(__FILE__);
'''
        # --- Method A (preferred): upload XML via WordPress Media REST API ---
        feed_url = f"{self.site_url}/wp-content/uploads/google-feed.xml"
        ok = False
        error_msg = ""
        size_bytes = len(xml_str.encode("utf-8"))

        media_url = self._upload_feed_via_media_api(xml_str)
        if media_url:
            feed_url = media_url
            ok = True
            logger.info("generate_google_feed: media upload OK → %s", feed_url)
        else:
            logger.info("generate_google_feed: media upload failed, trying plugin method...")

            # --- Method B (fallback): upload-and-run PHP plugin ---
            try:
                result = self._upload_and_run_plugin(php_code, "kairui_feed_gen.php", diag_filename=diag_file)
                ok = result.get("ok", False)
                error_msg = result.get("error", "")
                if ok:
                    logger.info("generate_google_feed: plugin method OK, verifying URL...")
                    try:
                        r = self.session.head(feed_url, timeout=10, allow_redirects=True)
                        if r.status_code >= 400:
                            logger.warning("generate_google_feed: feed URL returns %s", r.status_code)
                            ok = False
                    except Exception as e:
                        logger.warning("generate_google_feed: feed URL check failed: %s", e)
                        ok = False
            except Exception as e:
                logger.error("generate_google_feed: plugin method exception: %s", e)
                error_msg = str(e)

        return {
            "success": ok,
            "message": "Feed 生成成功" if ok else f"写入失败: {error_msg or '两种上传方式均失败'}",
            "products": len(products),
            "size_bytes": size_bytes,
            "feed_url": feed_url,
        }

    def inject_google_verification(self, verification_code: str, method: str = "meta") -> dict:
        """Inject Google Site Verification into the WordPress site.

        Args:
            verification_code: The verification string from Google (e.g. "abc123")
            method: "meta" (inject <meta> tag into <head>) or "html" (upload file)

        Returns:
            dict with success status
        """
        if not self._logged_in and not self.login():
            return {"success": False, "message": "登录失败"}

        if method == "html":
            # Upload the HTML verification file to wp-content
            html_content = f"google-site-verification: google{verification_code}.html"
            php_code = f'''<?php
/**
 * Plugin Name: Kairui GVerify HTML
 * Description: Writes Google verification HTML file and self-deletes
 */
$file = ABSPATH . 'google{verification_code}.html';
if (file_put_contents($file, '{html_content}')) {{
    echo json_encode(["ok" => true]);
}} else {{
    echo json_encode(["ok" => false, "error" => "write_failed"]);
}}
unlink(__FILE__);
'''
            try:
                result = self._upload_and_run_plugin(php_code, "kairui_gverify_html.php", diag_filename=None)
                return {
                    "success": result.get("ok", False),
                    "message": "验证文件已上传" if result.get("ok") else f"上传失败: {result.get('error', '')}",
                    "method": "html",
                    "verification_url": f"{self.site_url}/google{verification_code}.html",
                }
            except Exception as e:
                return {"success": False, "message": str(e)[:200], "method": "html"}

        # Default: meta tag via WordPress options / theme injection
        meta_tag = f'<meta name="google-site-verification" content="{verification_code}" />'

        # Use a self-deleting PHP plugin to inject the meta tag into wp_head
        php_code = f'''<?php
/**
 * Plugin Name: Kairui GVerify Meta
 * Description: Injects Google verification meta tag into theme header and self-deletes
 */
// Write meta tag into the active theme's header.php
$theme_dir = get_template_directory();
$header_file = $theme_dir . '/header.php';
$meta_tag = {__import__("json").dumps(meta_tag)};

if (file_exists($header_file)) {{
    $content = file_get_contents($header_file);
    if (strpos($content, $meta_tag) === false) {{
        // Insert after <head> or after wp_head()
        if (strpos($content, 'wp_head()') !== false) {{
            $content = str_replace('wp_head()', 'wp_head(); ?>' . "\\n" . $meta_tag . '<?php ', $content);
            file_put_contents($header_file, $content);
            echo json_encode(["ok" => true, "method" => "header_injection"]);
        }} else {{
            echo json_encode(["ok" => false, "error" => "no_wp_head", "method" => "header_injection"]);
        }}
    }} else {{
        echo json_encode(["ok" => true, "method" => "already_present"]);
    }}
}} else {{
    echo json_encode(["ok" => false, "error" => "no_header_file", "method" => "header_injection"]);
}}
unlink(__FILE__);
'''
        try:
            result = self._upload_and_run_plugin(php_code, "kairui_gverify_meta.php", diag_filename=None)
            success = result.get("ok", False)
            return {
                "success": success,
                "message": "验证标签已注入" if success else f"注入失败: {result.get('error', '')}",
                "method": "meta",
                "verification_code": verification_code,
            }
        except Exception as e:
            return {"success": False, "message": str(e)[:200], "method": "meta"}

    def inject_meta_tag(self, meta_html: str) -> dict:
        """Inject an arbitrary meta tag into the WordPress site header.

        Args:
            meta_html: Full meta tag HTML, e.g. '<meta name="xxx" content="yyy" />'

        Returns:
            dict with success status
        """
        if not self._logged_in and not self.login():
            return {"success": False, "message": "登录失败"}

        diag_file = "kairui_meta_inject_result.json"
        php_code = f'''<?php
/**
 * Plugin Name: Kairui Meta Inject
 * Description: Injects a meta tag into theme header (runs once)
 */
if (get_option('kairui_meta_injected')) {{
    return;
}}
$diag_file = WP_CONTENT_DIR . '/{diag_file}';
$theme_dir = get_template_directory();
$header_file = $theme_dir . '/header.php';
$meta_tag = {__import__("json").dumps(meta_html)};
$result = ["ok" => false, "error" => "unknown"];

if (file_exists($header_file)) {{
    $content = file_get_contents($header_file);
    if (strpos($content, $meta_tag) === false) {{
        if (strpos($content, 'wp_head()') !== false) {{
            $content = str_replace('wp_head()', 'wp_head(); ?>' . "\n" . $meta_tag . '<?php ', $content);
            file_put_contents($header_file, $content);
            $result = ["ok" => true, "method" => "header_injection"];
        }} else {{
            $result = ["ok" => false, "error" => "no_wp_head"];
        }}
    }} else {{
        $result = ["ok" => true, "method" => "already_present"];
    }}
}} else {{
    $result = ["ok" => false, "error" => "no_header_file"];
}}
update_option('kairui_meta_injected', 1);
file_put_contents($diag_file, json_encode($result));
'''
        try:
            result = self._upload_and_run_plugin(php_code, "kairui_meta_inject.php", diag_filename=diag_file)
            # Clean up the plugin from WordPress after successful execution
            self._deactivate_and_delete_plugin("kairui_meta_inject", "kairui_meta_inject.php")
            success = result.get("ok", False)
            return {
                "success": success,
                "message": "Meta标签已注入" if success else f"注入失败: {result.get('error', '')}",
            }
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from a string."""
        import re as _re
        return _re.sub(r'<[^>]+>', '', text).strip()

    def _upload_and_run_plugin(self, php_code: str, filename: str, diag_filename: str | None = None) -> dict:
        """Upload a self-deleting PHP plugin and execute it.

        Uploads the PHP file to wp-content/plugins/{filename}, activates it via
        wp-admin, and optionally reads back a diagnostic JSON file.

        Args:
            php_code: PHP source code (must call unlink(__FILE__) to self-delete)
            filename: Plugin file name (e.g. "kairui_feed_gen.php")
            diag_filename: If set, read back wp-content/{diag_filename} after execution

        Returns:
            dict from JSON response, or {"ok": false} on failure
        """
        import io

        # Upload the plugin
        upload_url = f"{self.site_url}/wp-admin/plugin-install.php?tab=upload"
        r_nonce = self.session.get(upload_url, timeout=15)
        up_nonce = re.search(r'name="_wpnonce" value="([^"]+)"', r_nonce.text)
        if not up_nonce:
            return {"ok": False, "error": "nonce_not_found"}
        wp_referer = re.search(r'name="_wp_http_referer" value="([^"]*)"', r_nonce.text)

        plugin_slug = filename.replace(".php", "")
        zip_buf = io.BytesIO()
        import zipfile as _zipfile
        with _zipfile.ZipFile(zip_buf, "w", _zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{plugin_slug}/{filename}", php_code)
        zip_buf.seek(0)

        files = {
            "pluginzip": (filename.replace(".php", ".zip"), zip_buf, "application/zip"),
            "_wpnonce": (None, up_nonce.group(1)),
        }
        if wp_referer:
            files["_wp_http_referer"] = (None, wp_referer.group(1))

        r_up = self.session.post(
            f"{self.site_url}/wp-admin/update.php?action=upload-plugin",
            files=files,
            timeout=30,
            allow_redirects=False,
        )

        if r_up.status_code not in (200, 302, 301):
            return {"ok": False, "error": f"upload_failed_http_{r_up.status_code}"}

        # Log redirect target and body excerpt to diagnose upload failures
        loc = r_up.headers.get("Location", "")
        logger.info("_upload_and_run_plugin: upload HTTP %s, Location=%s, body_head=%s",
                    r_up.status_code, loc[:120] if loc else "-", r_up.text[:300])
        # Search for WP error notices in the HTML response
        if r_up.status_code == 200 and not loc:
            for pattern in [r'<div id="message"[^>]*>(.*?)</div>', r'<div class="error"[^>]*>(.*?)</div>',
                           r'<div class="notice[^"]*"[^>]*>(.*?)</div>']:
                m = re.search(pattern, r_up.text, re.DOTALL | re.IGNORECASE)
                if m:
                    logger.warning("_upload_and_run_plugin: WP notice: %s", m.group(1).strip()[:300])

        direct_url = f"{self.site_url}/wp-content/plugins/{plugin_slug}/{filename}"

        # Verify the plugin file actually exists after upload
        try:
            r_check = self.session.head(direct_url, timeout=10)
            logger.info("_upload_and_run_plugin: file check %s → HTTP %s", plugin_slug, r_check.status_code)
            if r_check.status_code >= 400:
                logger.error("_upload_and_run_plugin: uploaded file NOT accessible (HTTP %s), upload may have failed silently", r_check.status_code)
        except Exception as e:
            logger.error("_upload_and_run_plugin: file check failed: %s", e)

        # Attempt admin activation with proper nonce from plugins page.
        logger.info("_upload_and_run_plugin: triggering execution for %s", plugin_slug)
        try:
            # Fetch plugins page to extract activation nonce
            r_plugs = self.session.get(f"{self.site_url}/wp-admin/plugins.php", timeout=15)
            escaped_slug = re.escape(f"{plugin_slug}/{filename}")
            act_match = re.search(
                rf'action=activate&amp;plugin={escaped_slug}[^"]*_wpnonce=([a-f0-9]+)',
                r_plugs.text
            )
            if act_match:
                nonce = act_match.group(1)
                act_url = f"{self.site_url}/wp-admin/plugins.php?action=activate&plugin={plugin_slug}/{filename}&_wpnonce={nonce}"
            else:
                logger.warning("_upload_and_run_plugin: could not find activation nonce, using URL without it")
                act_url = f"{self.site_url}/wp-admin/plugins.php?action=activate&plugin={plugin_slug}/{filename}&plugin_status=all"
            r_act = self.session.get(act_url, timeout=30, allow_redirects=True)
            logger.info("_upload_and_run_plugin: activation HTTP %s (url: %s)", r_act.status_code, r_act.url[:120])
        except Exception as e:
            logger.warning("_upload_and_run_plugin: activation request failed: %s", e)

        # Also hit the file directly (works with standalone PHP code)
        try:
            r_dir = self.session.get(direct_url, timeout=20)
            logger.info("_upload_and_run_plugin: direct access HTTP %s", r_dir.status_code)
        except Exception as e:
            logger.warning("_upload_and_run_plugin: direct access failed: %s", e)

        if diag_filename:
            for attempt in range(5):
                wait_sec = 3 + attempt * 2  # 3s, 5s, 7s, 9s, 11s
                time.sleep(wait_sec)
                diag = self._read_plugin_diag(diag_filename)
                if diag:
                    logger.info("_upload_and_run_plugin: diag read OK on attempt %d", attempt + 1)
                    return diag
                logger.debug("_upload_and_run_plugin: diag read attempt %d/5 failed", attempt + 1)
            logger.error("_upload_and_run_plugin: diag_timeout after 5 attempts for %s", diag_filename)
            return {"ok": False, "error": "diag_timeout: plugin may not have executed"}

        return {"ok": True}

    def _deactivate_and_delete_plugin(self, plugin_slug: str, filename: str) -> None:
        """Deactivate and delete a plugin from WordPress after execution.

        Args:
            plugin_slug: Plugin directory name (e.g. "kairui_meta_inject")
            filename: Plugin file name (e.g. "kairui_meta_inject.php")
        """
        plugin_path = f"{plugin_slug}/{filename}"
        try:
            # 1. Deactivate the plugin
            plugins_url = f"{self.site_url}/wp-admin/plugins.php"
            r = self.session.get(plugins_url, timeout=15)
            nonce_match = re.search(
                rf'action=deactivate&amp;plugin={re.escape(plugin_path)}[^"]*_wpnonce=([a-f0-9]+)',
                r.text
            )
            if nonce_match:
                nonce = nonce_match.group(1)
                deact_url = f"{plugins_url}?action=deactivate&plugin={plugin_path}&_wpnonce={nonce}"
                r2 = self.session.get(deact_url, timeout=15, allow_redirects=True)
                logger.info("_deactivate_and_delete_plugin: deactivate %s → HTTP %s", plugin_slug, r2.status_code)

            # 2. Delete the plugin
            r3 = self.session.get(plugins_url, timeout=15)
            del_nonce_match = re.search(r'wpnonce=([a-f0-9]+)', r3.text)
            if del_nonce_match:
                del_nonce = del_nonce_match.group(1)
                del_url = f"{plugins_url}?action=delete-selected&checked%5B0%5D={plugin_path}&_wpnonce={del_nonce}"
                r4 = self.session.get(del_url, timeout=15, allow_redirects=True)
                logger.info("_deactivate_and_delete_plugin: delete %s → HTTP %s", plugin_slug, r4.status_code)
        except Exception as e:
            logger.warning("_deactivate_and_delete_plugin: cleanup failed for %s: %s", plugin_slug, e)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _upload_feed_via_media_api(self, xml_content: str) -> str:
        """Upload feed XML to WordPress uploads directory directly via PHP.

        WordPress blocks .xml/.rss/.txt upload through both async-upload and REST API
        on many configurations.  We bypass this entirely by uploading a tiny PHP
        snippet via the plugin-install ZIP flow that writes the feed file directly
        to wp-content/uploads/ and returns its URL.
        """
        import base64

        b64_content = base64.b64encode(xml_content.encode("utf-8")).decode("ascii")
        feed_filename = "google-feed.rss"
        diag_filename = f"kairui_feed_upload_{int(time.time())}.json"

        php_code = f'''<?php
/**
 * Plugin Name: Kairui Feed Upload
 * Description: Writes feed file to uploads and self-deletes
 */
error_reporting(E_ALL); ini_set('display_errors', 0);
header('Content-Type: application/json');
try {{
    $base = dirname(__FILE__, 3);
    $target = $base . '/uploads/' . '{feed_filename}';
    $data = base64_decode('{b64_content}');
    if (file_put_contents($target, $data) === false) {{
        $r = ["ok" => false, "error" => "write_failed"];
    }} else {{
        $site_url = rtrim('{self.site_url}', '/');
        $url = $site_url . '/wp-content/uploads/' . '{feed_filename}';
        $r = ["ok" => true, "url" => $url, "size" => strlen($data)];
    }}
}} catch (Throwable $e) {{
    $r = ["ok" => false, "error" => $e->getMessage()];
}}
file_put_contents($base . '/{diag_filename}', json_encode($r));
echo json_encode($r);
unlink(__FILE__);
'''
        result = self._upload_and_run_plugin(php_code, "kairui_feed_upload.php", diag_filename)
        if result.get("ok") and result.get("url"):
            logger.info("_upload_feed: PHP OK → %s (%s bytes)", result["url"], result.get("size", 0))
            return result["url"]
        logger.warning("_upload_feed: PHP failed — %s", result.get("error", "unknown"))
        return ""

    def _read_plugin_diag(self, filename: str) -> dict | None:
        """Read diagnostic JSON written by a self-deleting plugin."""
        import json as _json
        try:
            r = self.session.get(
                f"{self.site_url}/wp-content/{filename}",
                timeout=10,
            )
            if r.status_code == 200:
                return _json.loads(r.text)
        except Exception:
            pass
        return None

    def setup_tax_rates(self, tax_config: dict) -> bool:
        """Configure WooCommerce tax settings and rates.

        tax_config keys:
          - tax_enabled: bool (default True)
          - prices_include_tax: bool (default False)
          - tax_rates: list of {"name": str, "rate": str, "country": str, "state": str, "shipping": bool, "priority": int}
        """
        if not self._logged_in and not self.login():
            return False
        try:
            # Step 1: General tax settings
            tax_enabled = "yes" if tax_config.get("tax_enabled", True) else "no"
            prices_include_tax = "yes" if tax_config.get("prices_include_tax", False) else "no"
            self.update_woocommerce_settings("tax", {
                "woocommerce_calc_taxes": tax_enabled,
                "woocommerce_prices_include_tax": prices_include_tax,
                "woocommerce_tax_based_on": "shipping",
                "woocommerce_shipping_tax_class": "inherit",
                "woocommerce_tax_round_at_subtotal": "no",
                "woocommerce_tax_display_shop": "excl",
                "woocommerce_tax_display_cart": "excl",
                "woocommerce_tax_total_display": "itemized",
            })
            logger.info("Tax general settings updated: enabled=%s, prices_include_tax=%s", tax_enabled, prices_include_tax)

            # Step 2: Create tax rates if specified
            rates = tax_config.get("tax_rates", [])
            if rates:
                rest_nonce = self._get_rest_nonce()
                headers = {}
                if rest_nonce:
                    headers["X-WP-Nonce"] = rest_nonce

                # Delete existing standard rates to avoid duplicates
                existing = self.session.get(
                    f"{self.site_url}/wp-json/wc/v3/taxes?class=standard&per_page=100",
                    headers=headers, timeout=self.timeout,
                )
                if existing.status_code == 200:
                    for t in existing.json():
                        self.session.delete(
                            f"{self.site_url}/wp-json/wc/v3/taxes/{t['id']}?force=true",
                            headers=headers, timeout=self.timeout,
                        )

                # Create new rates
                for rate in rates:
                    payload = {
                        "name": rate.get("name", "Tax"),
                        "rate": str(rate.get("rate", "0")),
                        "class": "standard",
                        "tax_rate_country": rate.get("country", "US"),
                        "tax_rate_state": rate.get("state", ""),
                        "tax_rate_shipping": "1" if rate.get("shipping", True) else "0",
                        "tax_rate_priority": rate.get("priority", 1),
                        "tax_rate_compound": "0",
                        "shipping": True,
                    }
                    r = self.session.post(
                        f"{self.site_url}/wp-json/wc/v3/taxes",
                        json=payload, headers=headers, timeout=self.timeout,
                    )
                    if r.status_code not in (200, 201):
                        logger.warning("create tax rate '%s' failed: %s %s", rate.get("name"), r.status_code, r.text[:200])
                logger.info("Tax rates configured: %d rates", len(rates))
            return True
        except Exception as e:
            logger.warning("setup_tax_rates error: %s", e)
            return False

    def setup_free_shipping(self, shipping_config: dict) -> bool:
        """Configure WooCommerce free shipping zone and method.

        shipping_config keys:
          - zone_name: str (default "Free Shipping")
          - country: str (default "US")  — comma-separated for multiple countries
          - min_amount: str/float (optional) — minimum order amount for free shipping
        """
        if not self._logged_in and not self.login():
            return False
        try:
            rest_nonce = self._get_rest_nonce()
            headers = {}
            if rest_nonce:
                headers["X-WP-Nonce"] = rest_nonce

            zone_name = shipping_config.get("zone_name", "Free Shipping")
            country = shipping_config.get("country", "US")
            min_amount = shipping_config.get("min_amount", "")

            # Delete existing free shipping zones with same name
            existing = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/shipping/zones",
                headers=headers, timeout=self.timeout,
            )
            if existing.status_code == 200:
                for z in existing.json():
                    if z.get("name") == zone_name:
                        self.session.delete(
                            f"{self.site_url}/wp-json/wc/v3/shipping/zones/{z['id']}?force=true",
                            headers=headers, timeout=self.timeout,
                        )

            # Create shipping zone
            r = self.session.post(
                f"{self.site_url}/wp-json/wc/v3/shipping/zones",
                json={"name": zone_name},
                headers=headers, timeout=self.timeout,
            )
            if r.status_code not in (200, 201):
                logger.warning("create shipping zone failed: %s %s", r.status_code, r.text[:200])
                return False

            zone = r.json()
            zone_id = zone["id"]
            logger.info("Shipping zone created: id=%s name=%s", zone_id, zone_name)

            # Add zone locations (countries)
            for c in country.split(","):
                c = c.strip()
                if c:
                    self.session.post(
                        f"{self.site_url}/wp-json/wc/v3/shipping/zones/{zone_id}/locations",
                        json={"code": c, "type": "country"},
                        headers=headers, timeout=self.timeout,
                    )

            # Create free_shipping method in the zone
            # Only send settings when min_amount is specified, otherwise let WC use defaults.
            # Setting "requires": "" via REST API can cause a PHP fatal on some WC versions.
            method_payload = {"method_id": "free_shipping"}
            if min_amount:
                method_payload["settings"] = {
                    "title": {"value": "Free Shipping"},
                    "requires": {"value": "min_amount"},
                    "min_amount": {"value": str(min_amount)},
                }

            r2 = self.session.post(
                f"{self.site_url}/wp-json/wc/v3/shipping/zones/{zone_id}/methods",
                json=method_payload, headers=headers, timeout=self.timeout,
            )
            if r2.status_code not in (200, 201):
                logger.warning("add free_shipping method failed: %s %s", r2.status_code, r2.text[:200])
            else:
                logger.info("Free shipping method added to zone %s", zone_id)

            # Delete any "Rest of the World" default zone if it exists (so free shipping is the only zone)
            existing2 = self.session.get(
                f"{self.site_url}/wp-json/wc/v3/shipping/zones",
                headers=headers, timeout=self.timeout,
            )
            if existing2.status_code == 200:
                for z in existing2.json():
                    if z.get("name") == "Locations not covered by your other zones":
                        self.session.delete(
                            f"{self.site_url}/wp-json/wc/v3/shipping/zones/{z['id']}?force=true",
                            headers=headers, timeout=self.timeout,
                        )
            return True
        except Exception as e:
            logger.warning("setup_free_shipping error: %s", e)
            return False

    def create_page(self, title: str, content: str) -> dict:
        """Create a WordPress page via REST API."""
        import re

        try:
            # Get REST API nonce from the new-page editor
            r = self.session.get(
                f"{self.site_url}/wp-admin/post-new.php?post_type=page",
                timeout=self.timeout,
            )
            nonce_match = re.search(r'wpApiSettings\s*=\s*\{[^}]*"nonce"\s*:\s*"([^"]+)"', r.text)
            if not nonce_match:
                nonce_match = re.search(r'"nonce"\s*:\s*"([a-f0-9]+)"', r.text)
            if not nonce_match:
                return {"success": False, "message": "REST API nonce not found"}

            headers = {"X-WP-Nonce": nonce_match.group(1)}
            r = self.session.post(
                f"{self.site_url}/wp-json/wp/v2/pages",
                json={"title": title, "content": content, "status": "publish"},
                headers=headers,
                timeout=self.timeout,
            )

            if r.status_code in (200, 201):
                data = r.json()
                return {"success": True, "post_id": data.get("id"),
                        "message": f"Page created: {title}"}

            return {"success": False, "message": f"REST API {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            logger.warning("create_page error: %s", e)
            return {"success": False, "message": str(e)[:200]}

    @staticmethod
    def _make_minimal_png() -> bytes:
        """Generate a minimal 64x64 solid-color PNG (no PIL dependency)."""
        import struct
        import zlib

        width, height = 64, 64
        # Raw pixel data: RGBA, each row filtered with 0
        raw = b""
        for y in range(height):
            raw += b"\x00"  # filter: none
            for x in range(width):
                raw += b"\x8b\x5c\xf5\xff"  # purple-ish RGBA

        def chunk(ctype, data):
            c = ctype + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )

    # ------------------------------------------------------------------
    # Public helpers for external feed / WooCommerce sync
    # ------------------------------------------------------------------

    def upload_feed_content(self, xml_content: str) -> str:
        """Upload an XML string as a feed file to WordPress. Returns URL or empty string."""
        if not self._logged_in and not self.login():
            logger.warning("upload_feed_content: login failed")
            return ""
        return self._upload_feed_via_media_api(xml_content)

    def delete_feed_file(self) -> bool:
        """Remove the feed file from WordPress uploads directory via PHP plugin."""
        if not self._logged_in and not self.login():
            return False
        php_code = '''<?php
/**
 * Plugin Name: Kairui Feed Clean
 * Description: Deletes feed files from uploads and self-deletes
 */
error_reporting(E_ALL); ini_set('display_errors', 0);
try {
    $base = dirname(__FILE__, 3);
    $uploads = $base . '/uploads';
    $files = glob($uploads . '/google-feed.*');
    $deleted = 0;
    foreach ($files as $f) {
        if (unlink($f)) $deleted++;
    }
    $r = ["ok" => true, "deleted" => $deleted];
} catch (Throwable $e) {
    $r = ["ok" => false, "error" => $e->getMessage()];
}
file_put_contents($base . '/kairui_feed_diag.json', json_encode($r));
echo json_encode($r);
unlink(__FILE__);
'''
        try:
            result = self._upload_and_run_plugin(php_code, "kairui_feed_clean.php")
            return result.get("ok", False)
        except Exception as e:
            logger.error("delete_feed_file error: %s", e)
            return False

    def create_woocommerce_products(self, products: list) -> dict:
        """Batch-create WooCommerce products via REST API. Returns {ok, fail, created}."""
        if not self._logged_in and not self.login():
            return {"ok": 0, "fail": len(products), "error": "登录失败"}

        rest_nonce = self._get_rest_nonce()
        headers = {"Content-Type": "application/json"}
        if rest_nonce:
            headers["X-WP-Nonce"] = rest_nonce

        # Ensure WooCommerce is active
        if not self.is_woocommerce_active():
            if not self.ensure_woocommerce():
                return {"ok": 0, "fail": len(products), "error": "WooCommerce 未激活"}

        ok = 0
        fail = 0
        created = []

        for p in products:
            try:
                # Build WC product payload
                name = (p.get("name") or "").strip()
                if not name:
                    fail += 1
                    continue

                extra = p.get("extra_data") or {}
                if isinstance(extra, str):
                    try:
                        extra = _json.loads(extra)
                    except Exception:
                        extra = {}
                product_type = extra.get("product_type", "simple")
                wc_attributes = extra.get("attributes") or []
                wc_variations = extra.get("variations") or []

                payload = {
                    "name": name,
                    "type": product_type,
                    "status": "publish",
                    "description": (p.get("description") or "")[:5000],
                    "short_description": (p.get("short_description") or "")[:500],
                }

                sku = p.get("sku", "").strip()
                if sku:
                    payload["sku"] = sku

                # For variable products, skip top-level price — let variations carry it
                if product_type != "variable":
                    price = p.get("regular_price", "").strip()
                    if price:
                        payload["regular_price"] = price
                    sale_price = p.get("sale_price", "").strip()
                    if sale_price:
                        payload["sale_price"] = sale_price

                # For variable products, include attributes in payload
                if product_type == "variable" and wc_attributes:
                    payload["attributes"] = wc_attributes

                stock_status = p.get("stock_status", "instock")
                payload["stock_status"] = stock_status

                # Categories
                categories_str = p.get("categories", "").strip()
                if categories_str:
                    cat_terms = [{"name": c.strip()} for c in categories_str.split(">") if c.strip()]
                    if cat_terms:
                        # Try to create/get categories
                        wc_cats = []
                        for ct in cat_terms:
                            try:
                                r = self.session.post(
                                    f"{self.site_url}/wp-json/wc/v3/products/categories",
                                    json={"name": ct["name"]},
                                    headers=headers,
                                    timeout=30,
                                )
                                if r.status_code in (200, 201):
                                    wc_cats.append({"id": r.json().get("id", 0)})
                                elif r.status_code == 400:
                                    # Category might already exist, try to find it
                                    sr = self.session.get(
                                        f"{self.site_url}/wp-json/wc/v3/products/categories",
                                        params={"search": ct["name"], "per_page": 1},
                                        headers=headers,
                                        timeout=30,
                                    )
                                    if sr.status_code == 200:
                                        results = sr.json()
                                        if results:
                                            wc_cats.append({"id": results[0]["id"]})
                            except Exception:
                                pass
                        if wc_cats:
                            payload["categories"] = wc_cats

                # Tags
                tags_str = p.get("tags", "").strip()
                if tags_str:
                    tag_terms = [{"name": t.strip()} for t in tags_str.split(",") if t.strip()]
                    if tag_terms:
                        wc_tags = []
                        for tt in tag_terms[:5]:
                            try:
                                r = self.session.post(
                                    f"{self.site_url}/wp-json/wc/v3/products/tags",
                                    json={"name": tt["name"]},
                                    headers=headers,
                                    timeout=30,
                                )
                                if r.status_code in (200, 201):
                                    wc_tags.append({"id": r.json().get("id", 0)})
                            except Exception:
                                pass
                        if wc_tags:
                            payload["tags"] = wc_tags

                # Images — upload media first
                images_str = p.get("images", "")
                if images_str:
                    img_urls = [u.strip() for u in images_str.split("|") if u.strip()]
                    wc_images = []
                    for img_url in img_urls[:5]:  # max 5 images per product
                        try:
                            img_data = http_requests.get(img_url, timeout=30, verify=False).content
                            img_name = img_url.split("/")[-1].split("?")[0] or "product.jpg"
                            if not img_name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                                img_name += ".jpg"
                            r = self.session.post(
                                f"{self.site_url}/wp-json/wp/v2/media",
                                files={"file": (img_name, img_data)},
                                headers={"X-WP-Nonce": rest_nonce} if rest_nonce else {},
                                timeout=60,
                            )
                            if r.status_code in (200, 201):
                                wc_images.append({"src": r.json().get("source_url", img_url)})
                        except Exception:
                            pass
                    if wc_images:
                        payload["images"] = wc_images

                # Create product
                r = self.session.post(
                    f"{self.site_url}/wp-json/wc/v3/products",
                    json=payload,
                    headers=headers,
                    timeout=60,
                )
                if r.status_code in (200, 201):
                    product_data = r.json()
                    parent_id = product_data.get("id")
                    ok += 1
                    created.append(parent_id)
                    logger.info(f"create_woocommerce_products: OK '{name[:40]}' id={parent_id}")

                    # Create variations for variable products
                    if product_type == "variable" and wc_variations and parent_id:
                        for vi, v in enumerate(wc_variations):
                            try:
                                v_payload = {
                                    "sku": (v.get("sku") or "")[:255],
                                    "regular_price": str(v.get("regular_price", "")).strip() or "0",
                                    "stock_status": v.get("stock_status", "instock"),
                                    "attributes": v.get("attributes") or [],
                                }

                                # Upload variant image if present
                                v_img = v.get("image", "").strip()
                                if v_img:
                                    try:
                                        img_data = http_requests.get(v_img, timeout=30, verify=False).content
                                        img_name = v_img.split("/")[-1].split("?")[0] or "variant.jpg"
                                        if not img_name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                                            img_name += ".jpg"
                                        mr = self.session.post(
                                            f"{self.site_url}/wp-json/wp/v2/media",
                                            files={"file": (img_name, img_data)},
                                            headers={"X-WP-Nonce": rest_nonce} if rest_nonce else {},
                                            timeout=60,
                                        )
                                        if mr.status_code in (200, 201):
                                            v_payload["image"] = {"src": mr.json().get("source_url", v_img)}
                                    except Exception:
                                        pass

                                vr = self.session.post(
                                    f"{self.site_url}/wp-json/wc/v3/products/{parent_id}/variations",
                                    json=v_payload,
                                    headers=headers,
                                    timeout=60,
                                )
                                if vr.status_code in (200, 201):
                                    logger.info(
                                        f"create_woocommerce_products: variation OK "
                                        f"'{name[:30]}' [{vi+1}/{len(wc_variations)}] "
                                        f"sku={v.get('sku','')[:20]}"
                                    )
                                else:
                                    logger.warning(
                                        f"create_woocommerce_products: variation FAIL "
                                        f"'{name[:30]}' HTTP {vr.status_code}: {vr.text[:200]}"
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"create_woocommerce_products: variation error "
                                    f"'{name[:30]}': {e}"
                                )
                else:
                    fail += 1
                    logger.warning(f"create_woocommerce_products: FAIL '{name[:40]}' HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                fail += 1
                logger.warning(f"create_woocommerce_products: error '{p.get('name', '')[:40]}': {e}")

        return {"ok": ok, "fail": fail, "created": created}

    def delete_all_woocommerce_products(self) -> dict:
        """Delete ALL products from WooCommerce store. Returns {deleted, failed}."""
        if not self._logged_in and not self.login():
            return {"deleted": 0, "failed": 0, "error": "登录失败"}

        rest_nonce = self._get_rest_nonce()
        headers = {"Content-Type": "application/json"}
        if rest_nonce:
            headers["X-WP-Nonce"] = rest_nonce

        if not self.is_woocommerce_active():
            return {"deleted": 0, "failed": 0, "error": "WooCommerce 未激活"}

        deleted = 0
        failed = 0

        # Fetch all products and delete them in batches
        per_page = 100
        page = 1
        while True:
            try:
                r = self.session.get(
                    f"{self.site_url}/wp-json/wc/v3/products",
                    params={"per_page": per_page, "page": page, "status": "any"},
                    headers=headers,
                    timeout=60,
                )
                if r.status_code != 200:
                    break
                batch = r.json()
                if not isinstance(batch, list) or not batch:
                    break

                for prod in batch:
                    pid = prod.get("id")
                    if not pid:
                        continue
                    try:
                        dr = self.session.delete(
                            f"{self.site_url}/wp-json/wc/v3/products/{pid}",
                            params={"force": "true"},
                            headers=headers,
                            timeout=30,
                        )
                        if dr.status_code in (200, 201):
                            deleted += 1
                        else:
                            failed += 1
                            logger.warning(f"delete_product {pid}: HTTP {dr.status_code}")
                    except Exception as e:
                        failed += 1
                        logger.warning(f"delete_product {pid} error: {e}")

                if len(batch) < per_page:
                    break
                page += 1
            except Exception as e:
                logger.warning(f"delete_all_products fetch error: {e}")
                break

        logger.info(f"delete_all_woocommerce_products: deleted={deleted} failed={failed}")
        return {"deleted": deleted, "failed": failed}
