import functools
import hashlib
import json
import logging
import os
import re
import traceback
from datetime import datetime
import subprocess
import threading
import time
import uuid

import requests as http_requests

from flask import current_app, jsonify, redirect, request, Response, send_file
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from config import config
from services.mc_auto_register import get_profiles_root, resolve_profile_path
from task_logs import add_log, complete_task, create_task, get_task_logs, get_diagnosis
from bridge_routes import register_bridge_routes
from models import (
    assign_cloakbrowser_profile_to_brand_kit,
    create_bg_task,
    create_brand_kit,
    create_cf_account,
    create_feed_product,
    create_fingerprint_category,
    create_panel_environment,
    create_sample_feed_products,
    create_site,
    create_user,
    delete_brand_kit,
    delete_cf_account,
    delete_feed_product,
    delete_fingerprint_category,
    delete_panel_environment,
    delete_site,
    delete_user,
    get_all_fingerprint_categories,
    get_all_panel_environments,
    get_all_users,
    get_available_cloakbrowser_profile,
    get_bg_task,
    get_bg_task_by_site,
    get_brand_kit,
    get_cf_account,
    get_db,
    get_default_cf_account,
    get_enabled_plugins,
    get_environment_by_cf_account,
    get_feed_product,
    get_feed_stats,
    get_global_config,
    get_panel_environment,
    get_profile_category,
    get_site,
    get_user_by_id,
    get_user_by_username,
    get_user_panel_environment,
    init_db,
    list_brand_kits,
    list_cf_accounts,
    list_feed_products,
    list_profile_categories,
    list_sites,
    release_cloakbrowser_profile_from_brand_kit,
    set_default_cf_account,
    set_default_panel_environment,
    set_profile_category,
    clear_amazon_search_results,
    clear_generated_feed,
    clear_woocommerce_products,
    delete_amazon_search_results,
    delete_generated_feed_items,
    delete_woocommerce_products,
    list_generated_feed,
    list_woocommerce_products,
    list_walmart_categories_from_db,
    load_amazon_search_results,
    load_walmart_products,
    save_amazon_search_results,
    save_generated_feed_product,
    save_woocommerce_product,
    save_walmart_products,
    update_bg_task,
    update_brand_kit,
    get_available_google_accounts,
    get_available_proxies,
    list_deprecated_proxies,
    get_google_account,
    get_proxy,
    delete_google_account,
    import_google_accounts_from_txt,
    list_google_accounts,
    import_proxies_from_text,
    list_proxies,
    reseed_proxies,
    update_feed_product,
    update_global_config,
    update_panel_environment,
    update_site,
    update_site_fields,
    update_user,
    create_static_site_product,
    get_static_site_product,
    list_static_site_products,
    update_static_site_product,
    delete_static_site_product,
    import_products_to_site,
    get_site as get_site_by_id,
)
from panel_client import panel_client, OnePanelClient
from wordpress_com_client import WordPressComClient
from services.walmart_service import (
    CrawlbaseWalmartService,
    WalmartServiceError,
    CrawlbaseAuthError,
    CrawlbaseRateLimitError,
    CrawlbaseParseError,
    CrawlbaseTimeoutError,
)
from services.export_utils import DataExportUtil
from services.wordpress_client import WordPressAdminSession

logger = logging.getLogger(__name__)

# Directory to store uploaded plugin files
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")
os.makedirs(PLUGIN_DIR, exist_ok=True)


def _next_cf_account_name():
    """Generate the next sequential Cloudflare account name: kairui1, kairui2, ..."""
    existing = [acct["name"] for acct in list_cf_accounts()]
    n = 1
    while f"kairui{n}" in existing:
        n += 1
    return f"kairui{n}"


def _get_panel_client():
    """Get a OnePanelClient configured for the current user's panel environment.
    Falls back to default panel_client when JWT is unavailable (e.g. background threads)."""
    try:
        claims = get_jwt()
        user_id = claims.get("user_id")
        if user_id:
            env = get_user_panel_environment(user_id)
            if env:
                return OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"])
    except Exception:
        pass
    return panel_client


def install_plugins_to_site(site_url, admin_user, admin_password, plugin_ids):
    """Install plugins to a WordPress site via wp-admin HTTP API.
    
    Steps:
    1. Login to wp-admin via HTTP
    2. Get upload nonce from plugin-install page
    3. Upload each plugin zip via wp-admin/update.php
    4. Activate each plugin via wp-admin/plugins.php
    """
    import re as _re
    
    results = []
    
    try:
        # Login to WordPress
        session = http_requests.Session()
        login_resp = session.post(
            f"{site_url}/wp-login.php",
            data={
                "log": admin_user,
                "pwd": admin_password,
                "wp-submit": "Log In",
                "redirect_to": f"{site_url}/wp-admin/",
            },
            timeout=30,
            allow_redirects=False,
        )
        if login_resp.status_code not in (302, 200):
            return [{"plugin": p, "status": "error", "message": "WordPress登录失败"} for p in plugin_ids]
        verify = session.get(f"{site_url}/wp-admin/", timeout=30, allow_redirects=True)
        if "wp-admin-bar" not in verify.text and "dashboard" not in verify.text.lower():
            return [{"plugin": p, "status": "error", "message": "WordPress登录失败"} for p in plugin_ids]
        
        for pid in plugin_ids:
            plugin = get_plugin(pid)
            if not plugin or not plugin.get("file_path") or not os.path.isfile(plugin["file_path"]):
                results.append({"plugin": pid, "status": "error", "message": "插件文件不存在"})
                continue

            plugin_name = plugin["name"]
            plugin_filename = plugin["filename"]

            try:
                # Get upload nonce from plugin-install page
                upload_page = session.get(
                    f"{site_url}/wp-admin/plugin-install.php?tab=upload",
                    timeout=15,
                )
                nonce_match = _re.search(r'_wpnonce["\']\s*value=["\']([^"\']+)', upload_page.text)
                if not nonce_match:
                    results.append({
                        "plugin": plugin_name, "status": "error",
                        "message": "无法获取上传nonce"
                    })
                    continue
                nonce = nonce_match.group(1)

                # Upload plugin zip
                with open(plugin["file_path"], "rb") as f:
                    upload_resp = session.post(
                        f"{site_url}/wp-admin/update.php?action=upload-plugin",
                        files={"pluginzip": (plugin_filename, f, "application/zip")},
                        data={"_wpnonce": nonce},
                        timeout=60,
                    )

                if upload_resp.status_code != 200:
                    results.append({
                        "plugin": plugin_name, "status": "error",
                        "message": f"上传失败: HTTP {upload_resp.status_code}"
                    })
                    continue

                # Check if plugin was installed and find activation link
                act_match = _re.search(
                    r'action=activate[^"]*plugin=([^"&]+)[^"]*_wpnonce=([a-f0-9]+)',
                    upload_resp.text,
                )
                if act_match:
                    plugin_path = act_match.group(1)
                    act_nonce = act_match.group(2)
                    
                    # Activate the plugin
                    act_resp = session.get(
                        f"{site_url}/wp-admin/plugins.php?action=activate&plugin={plugin_path}&_wpnonce={act_nonce}",
                        timeout=30,
                        allow_redirects=True,
                    )
                    if act_resp.status_code == 200:
                        results.append({
                            "plugin": plugin_name, "status": "success",
                            "message": "插件已安装并启用"
                        })
                    else:
                        results.append({
                            "plugin": plugin_name, "status": "success",
                            "message": "插件已安装，启用需确认"
                        })
                elif "已安装" in upload_resp.text or "already" in upload_resp.text.lower():
                    # Plugin already exists — try to activate it
                    activated = False
                    act_match2 = _re.search(
                        r'action=activate[^"]*plugin=([^"&]+)[^"]*_wpnonce=([a-f0-9]+)',
                        upload_resp.text,
                    )
                    if act_match2:
                        act_resp2 = session.get(
                            f"{site_url}/wp-admin/plugins.php?action=activate&plugin={act_match2.group(1)}&_wpnonce={act_match2.group(2)}",
                            timeout=30,
                            allow_redirects=True,
                        )
                        activated = act_resp2.status_code == 200
                    if not activated:
                        # Check plugins page for activate link
                        r_pl = session.get(f"{site_url}/wp-admin/plugins.php", timeout=15)
                        file_match = _re.search(
                            rf'action=activate[^"]*plugin=({re.escape(plugin_filename.replace(".zip", ""))}[^"&]+)[^"]*_wpnonce=([a-f0-9]+)',
                            r_pl.text,
                        )
                        if file_match:
                            act_resp2 = session.get(
                                f"{site_url}/wp-admin/plugins.php?action=activate&plugin={file_match.group(1)}&_wpnonce={file_match.group(2)}",
                                timeout=30,
                                allow_redirects=True,
                            )
                            activated = act_resp2.status_code == 200
                    results.append({
                        "plugin": plugin_name, "status": "success",
                        "message": "插件已存在" + ("并启用" if activated else "")
                    })
                else:
                    results.append({
                        "plugin": plugin_name, "status": "error",
                        "message": "上传后未找到激活链接"
                    })

            except http_requests.Timeout:
                results.append({"plugin": plugin_name, "status": "error", "message": "请求超时"})
            except Exception as e:
                results.append({"plugin": plugin_name, "status": "error", "message": str(e)[:100]})

    except Exception as e:
        logger.error(f"Plugin install session error: {e}")
        results = [{"plugin": p, "status": "error", "message": f"会话错误: {str(e)[:80]}"} for p in plugin_ids]

    return results


THEMES_DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "themes-default")

def _ensure_theme_bundled_plugins():
    """Return path to woodmart.zip from themes-default (Docker image, updated on each build)."""
    theme_zip = os.path.join(THEMES_DEFAULT_DIR, "woodmart.zip")
    if not os.path.isfile(theme_zip):
        logger.warning("woodmart.zip not found at %s", theme_zip)
    return theme_zip


def _scan_theme_zips():
    """Scan themes-default/ for .zip files. Returns [(name, path), ...].

    Uses themes-default (Docker image) rather than themes/ (volume) so
    git pull + docker compose build always picks up the latest zip.
    """
    _ensure_theme_bundled_plugins()
    zips = []
    if os.path.isdir(THEMES_DEFAULT_DIR):
        for f in sorted(os.listdir(THEMES_DEFAULT_DIR)):
            if f.lower().endswith('.zip'):
                zips.append((f.replace('.zip', ''), os.path.join(THEMES_DEFAULT_DIR, f)))
    return zips


PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")


def _scan_plugin_zips():
    """返回 backend/plugins/ 下所有 .zip → [(slug, path), ...]"""
    zips = []
    if os.path.isdir(PLUGINS_DIR):
        for f in sorted(os.listdir(PLUGINS_DIR)):
            if f.lower().endswith('.zip'):
                zips.append((f.replace('.zip', ''), os.path.join(PLUGINS_DIR, f)))
    return zips


def _ensure_plugins_cached(slugs):
    """下载缺失的插件 ZIP 到 backend/plugins/，已存在的跳过"""
    os.makedirs(PLUGINS_DIR, exist_ok=True)
    results = []
    for slug in slugs:
        dest = os.path.join(PLUGINS_DIR, f"{slug}.zip")
        if os.path.isfile(dest) and os.path.getsize(dest) > 100:
            results.append({"slug": slug, "cached": True, "size": os.path.getsize(dest)})
            continue
        dl_url = f"https://downloads.wordpress.org/plugin/{slug}.latest-stable.zip"
        try:
            r = http_requests.get(dl_url, timeout=120, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 100:
                with open(dest, 'wb') as f:
                    f.write(r.content)
                results.append({"slug": slug, "cached": True, "size": len(r.content)})
            else:
                results.append({"slug": slug, "cached": False, "error": f"HTTP {r.status_code}"})
        except Exception as e:
            results.append({"slug": slug, "cached": False, "error": str(e)[:100]})
    return results



def install_themes_to_site(site_url, admin_user, admin_password, theme_files, ip_url=None):
    """Install themes to a WordPress site via wp-admin HTTP API.

    Args:
        theme_files: list of (name, file_path) tuples, or None to auto-scan backend/themes/
    
    Steps:
    1. Login to wp-admin via HTTP
    2. Get upload nonce from theme-install page
    3. Upload each theme zip via wp-admin/update.php
    4. Activate the last theme via wp-admin/themes.php
    """
    import re as _re

    _install_url = ip_url or site_url
    results = []

    try:
        session = http_requests.Session()
        login_resp = session.post(
            f"{_install_url}/wp-login.php",
            data={
                "log": admin_user,
                "pwd": admin_password,
                "wp-submit": "Log In",
                "redirect_to": f"{_install_url}/wp-admin/",
            },
            timeout=120,
            allow_redirects=False,
        )
        if login_resp.status_code not in (302, 200):
            return [{"theme": "all", "status": "error", "message": "WordPress登录失败"}]
        # Verify cookies work — freshly-installed WP may return 200
        verify = session.get(f"{_install_url}/wp-admin/", timeout=30, allow_redirects=True)
        if "wp-admin-bar" not in verify.text and "dashboard" not in verify.text.lower():
            return [{"theme": "all", "status": "error", "message": "WordPress登录失败"}]

        last_theme_stylesheet = None
        
        for theme_name, theme_path in theme_files:
            theme_filename = os.path.basename(theme_path)

            try:
                # Get upload nonce from theme-install page
                upload_page = session.get(
                    f"{_install_url}/wp-admin/theme-install.php?upload",
                    timeout=15,
                )
                # Try multiple nonce patterns (WordPress wraps nonce in different ways)
                nonce_match = _re.search(r'_wpnonce["\']\s*value=["\']([^"\']+)', upload_page.text)
                if not nonce_match:
                    nonce_match = _re.search(r'name="_wpnonce"\s+value="([^"]+)"', upload_page.text)
                if not nonce_match:
                    nonce_match = _re.search(r'_wpnonce=([a-f0-9]+)', upload_page.text)
                if not nonce_match:
                    # Log the page snippet for debugging
                    snippet = upload_page.text[upload_page.text.find('_wpnonce'):upload_page.text.find('_wpnonce')+200] if '_wpnonce' in upload_page.text else '(not found)'
                    logger.warning("Theme nonce not found for %s. Snippet: %s", site_url, snippet[:120])
                    results.append({"theme": theme_name, "status": "error", "message": "无法获取上传nonce"})
                    continue
                nonce = nonce_match.group(1)
                logger.info("Theme nonce for %s: %s... (page size=%d)", theme_name, nonce[:8], len(upload_page.text))

                # Upload theme zip
                max_retries = 2
                for attempt in range(max_retries):
                    with open(theme_path, "rb") as f:
                        upload_resp = session.post(
                            f"{_install_url}/wp-admin/update.php?action=upload-theme",
                            files={"themezip": (theme_filename, f, "application/zip")},
                            data={"_wpnonce": nonce},
                            timeout=180,
                            allow_redirects=False,
                        )
                    logger.info("Theme upload %s: HTTP %s, Location=%s, body_len=%d",
                               theme_name, upload_resp.status_code,
                               upload_resp.headers.get("Location", "-")[:120],
                               len(upload_resp.text))
                    # WordPress redirects on success (302→themes.php) and on "already exists" (200)
                    upload_ok = upload_resp.status_code in (200, 302)
                    if upload_ok:
                        break
                    # Check if session expired (redirected to login)
                    if "wp-login" in (upload_resp.url or ""):
                        logger.warning("Theme upload: session expired (redirected to login)")
                        # Re-login and get fresh nonce
                        session.post(f"{_install_url}/wp-login.php", data={
                            "log": admin_user, "pwd": admin_password,
                            "wp-submit": "Log In", "redirect_to": f"{_install_url}/wp-admin/",
                        }, timeout=30, allow_redirects=True)
                        up2 = session.get(f"{_install_url}/wp-admin/theme-install.php?upload", timeout=15)
                        nm2 = _re.search(r'name="_wpnonce"\s+value="([^"]+)"', up2.text)
                        if nm2:
                            nonce = nm2.group(1)
                            logger.info("Theme nonce refreshed: %s...", nonce[:8])
                    if attempt < max_retries - 1:
                        # 403 may indicate filesize limit — wait for .user.ini to take effect
                        logger.warning("Theme upload attempt %d HTTP %s @%s — retrying after delay",
                                       attempt + 1, upload_resp.status_code, upload_resp.url[:80])
                        time.sleep(15)

                if upload_resp.status_code not in (200, 302):
                    err_text = upload_resp.text
                    err_match = _re.search(r'<p[^>]*>(.*?)</p>', err_text, _re.DOTALL)
                    err_msg = err_match.group(1).strip()[:150] if err_match else err_text[:200]
                    logger.warning("Theme upload failed: HTTP %s, error=%s", upload_resp.status_code, err_msg)
                    results.append({"theme": theme_name, "status": "error", "message": f"上传失败: HTTP {upload_resp.status_code}"})
                    continue

                # Check for upload errors in the response body BEFORE treating it as success.
                # 302 responses have no body; error is in the redirect URL query string.
                upload_error = None
                if upload_resp.status_code == 302:
                    loc = upload_resp.headers.get("Location", "")
                    if "error=" in loc or "upload" in loc:
                        upload_error = f"upload redirected back: {loc[:200]}"
                elif upload_resp.status_code == 200:
                    t = upload_resp.text.lower()
                    # Check for real errors first (not status messages like "Unpacking the package…")
                    for err_kw in ["style.css stylesheet", "not a valid theme",
                                   "are you sure", "invalid nonce", "missing temporary",
                                   "installation failed", "could not copy file",
                                   "incompatible archive"]:
                        if err_kw in t:
                            snippet = _re.search(r'<p[^>]*>(.*?)</p>', upload_resp.text, _re.DOTALL)
                            upload_error = snippet.group(1).strip()[:200] if snippet else upload_resp.text[:200]
                            break
                if upload_error:
                    logger.warning("Theme upload error for %s: %s", theme_name, upload_error)
                    results.append({"theme": theme_name, "status": "error", "message": f"上传失败: {upload_error[:100]}"})
                    continue

                # Determine result: 200=response body, 302=redirect (success, no body)
                already_exists = upload_resp.status_code == 200 and (
                    "already exists" in upload_resp.text.lower() or "destination folder already exists" in upload_resp.text.lower()
                )

                if already_exists:
                    # Theme already exists — delete old and re-upload to refresh bundled plugins.
                    existing_match = _re.search(r'stylesheet=([^"&]+)', upload_resp.text)
                    old_stylesheet = existing_match.group(1) if existing_match else None
                    reuploaded = False
                    if old_stylesheet:
                        try:
                            tp = session.get(f"{_install_url}/wp-admin/themes.php", timeout=15, allow_redirects=True)
                            if _re.search(rf'data-slug="{_re.escape(old_stylesheet)}".*?"active"', tp.text, _re.DOTALL):
                                switch_nonce = _re.search(
                                    r'action=activate[^"]*stylesheet=(twentytwentyfive)[^"]*_wpnonce=([a-f0-9]+)',
                                    tp.text)
                                if not switch_nonce:
                                    switch_nonce = _re.search(
                                        r'action=activate[^"]*stylesheet=(twentytwenty[^"&]*)[^"]*_wpnonce=([a-f0-9]+)',
                                        tp.text)
                                if switch_nonce:
                                    fb = switch_nonce.group(1)
                                    session.get(
                                        f"{_install_url}/wp-admin/themes.php?action=activate&stylesheet={fb}&_wpnonce={switch_nonce.group(2)}",
                                        timeout=30, allow_redirects=True)
                                    tp = session.get(f"{_install_url}/wp-admin/themes.php", timeout=15, allow_redirects=True)
                            del_nonce = _re.search(
                                rf'action=delete[^"]*stylesheet={_re.escape(old_stylesheet)}[^"]*_wpnonce=([a-f0-9]+)',
                                tp.text)
                            if del_nonce:
                                session.get(
                                    f"{_install_url}/wp-admin/themes.php?action=delete&stylesheet={old_stylesheet}&_wpnonce={del_nonce.group(1)}",
                                    timeout=30, allow_redirects=True)
                                with open(theme_path, "rb") as f:
                                    reup = session.post(
                                        f"{_install_url}/wp-admin/update.php?action=upload-theme",
                                        files={"themezip": (theme_filename, f, "application/zip")},
                                        data={"_wpnonce": nonce}, timeout=300, allow_redirects=False)
                                if reup.status_code in (200, 302):
                                    # Activate the re-uploaded theme via themes.php
                                    tp2 = session.get(f"{_install_url}/wp-admin/themes.php", timeout=15, allow_redirects=True)
                                    # Find stylesheet of the re-uploaded theme
                                    theme_slug = theme_name.lower().replace(' ', '-')
                                    act_nonce2 = _re.search(
                                        rf'stylesheet=({_re.escape(theme_slug)})[^"]*_wpnonce=([a-f0-9]+)',
                                        tp2.text)
                                    if not act_nonce2:
                                        act_nonce2 = _re.search(
                                            rf'stylesheet=({_re.escape(theme_name)})[^"]*_wpnonce=([a-f0-9]+)',
                                            tp2.text)
                                    if act_nonce2:
                                        last_theme_stylesheet = act_nonce2.group(1)
                                        session.get(
                                            f"{_install_url}/wp-admin/themes.php?action=activate&stylesheet={act_nonce2.group(1)}&_wpnonce={act_nonce2.group(2)}",
                                            timeout=30, allow_redirects=True)
                                        results.append({"theme": theme_name, "status": "success", "message": "主题已覆盖安装并启用"})
                                        reuploaded = True
                        except Exception as e:
                            logger.warning("Theme overwrite failed for %s: %s", theme_name, e)
                    if not reuploaded:
                        if old_stylesheet:
                            last_theme_stylesheet = old_stylesheet
                        results.append({"theme": theme_name, "status": "success", "message": "主题已存在"})
                else:
                    # Upload succeeded (200 with no error or 302 redirect).
                    # Activate the theme via themes.php.
                    tp = session.get(f"{_install_url}/wp-admin/themes.php", timeout=15, allow_redirects=True)
                    tpt = tp.text
                    tlen = len(tpt)
                    # Check for WP fatal error page BEFORE anything else
                    fatal_match = _re.search(r'(Fatal error|Parse error|There has been a critical error|briefly unavailable)', tpt)
                    if fatal_match:
                        logger.warning("Theme activate: fatal error detected on themes.php, skipping")
                        results.append({"theme": theme_name, "status": "error", "message": f"WP致命错误: {fatal_match.group(1)}"})
                        continue
                    # Search for any nonce linked to this theme's stylesheet.
                    # WordPress encodes & as &amp; in hrefs but may also use
                    # &#038; in admin notices; search for all three separators.
                    theme_slug = theme_name.lower().replace(' ', '-')
                    act = None
                    for sep in [r'&amp;', r'&#038;', r'&']:
                        act = _re.search(
                            rf'stylesheet=({_re.escape(theme_slug)})[^"]*{sep}_wpnonce=([a-f0-9]+)',
                            tpt)
                        if act:
                            break
                    if not act:
                        # Try with original casing
                        for sep in [r'&amp;', r'&#038;', r'&']:
                            act = _re.search(
                                rf'stylesheet=({_re.escape(theme_name)})[^"]*{sep}_wpnonce=([a-f0-9]+)',
                                tpt)
                            if act:
                                break
                    # If still not found, scan ALL activation links on the page
                    if not act:
                        all_activate = _re.findall(r'stylesheet=([^"&]+)[^"]*_wpnonce=([a-f0-9]+)', tpt)
                        logger.warning("Theme activate: '%s' not found on themes.php (len=%d). All activate links: %s",
                                       theme_slug, tlen, [a[0] for a in all_activate])
                        # Last resort: the uploaded theme may have a different folder name.
                        # Scan for any non-default theme's activate link.
                        for a_stylesheet, a_nonce in all_activate:
                            if a_stylesheet not in ('twentytwentyfive', 'twentytwentyfour', 'twentytwentythree'):
                                act = _re.search(rf'stylesheet=({_re.escape(a_stylesheet)})[^"]*_wpnonce=({_re.escape(a_nonce)})', tpt)
                                logger.warning("Theme activate: falling back to '%s' (was looking for '%s')",
                                               a_stylesheet, theme_slug)
                                break
                    if act:
                        last_theme_stylesheet = act.group(1)
                        session.get(
                            f"{_install_url}/wp-admin/themes.php?action=activate&stylesheet={act.group(1)}&_wpnonce={act.group(2)}",
                            timeout=30, allow_redirects=True)
                        logger.info("Theme activate: %s activated as '%s'", theme_name, last_theme_stylesheet)
                        results.append({"theme": theme_name, "status": "success", "message": "主题已安装并启用"})
                    else:
                        logger.warning("Theme activate: no activate link found for %s (themes.php len=%d, contains '%s'=%s)",
                                       theme_name, tlen, theme_slug, theme_slug in tpt.lower())
                        results.append({"theme": theme_name, "status": "success", "message": "主题已上传"})

            except http_requests.Timeout:
                results.append({"theme": theme_name, "status": "error", "message": "请求超时"})
            except Exception as e:
                results.append({"theme": theme_name, "status": "error", "message": str(e)[:100]})

        # Activate the last theme if not yet activated
        if last_theme_stylesheet and results:
            last_result = results[-1]
            if last_result.get("status") == "success" and "启用" not in last_result.get("message", ""):
                try:
                    themes_page = session.get(f"{_install_url}/wp-admin/themes.php", timeout=15)
                    act_nonce = _re.search(
                        rf'stylesheet={_re.escape(last_theme_stylesheet)}[^"]*_wpnonce=([a-f0-9]+)',
                        themes_page.text,
                    )
                    if act_nonce:
                        session.get(
                            f"{_install_url}/wp-admin/themes.php?action=activate&stylesheet={last_theme_stylesheet}&_wpnonce={act_nonce.group(1)}",
                            timeout=30,
                            allow_redirects=True,
                        )
                        last_result["message"] = "主题已安装并启用"
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Theme install session error: {e}")
        results = [{"theme": "all", "status": "error", "message": f"会话错误: {str(e)[:80]}"}]

    return results


def _fix_wp_siteurl(_install_url, admin_user, admin_password, wp_url, _log):
    """Fix siteurl/home — trigger MU plugin via domain URL.

    The persistent MU plugin (wp-timeout-fix) handles the actual fix:
    on first request with HTTP_HOST set to the domain, it updates
    siteurl/home options in the database stripping any port numbers.
    """
    try:
        r = http_requests.get(f"{wp_url}/", timeout=15)
        _log(f"siteurl fix: domain trigger {wp_url} → HTTP {r.status_code}")
    except Exception as e:
        _log(f"siteurl fix: domain trigger failed for {wp_url}: {e}")


def auto_install_wordpress(container_name, site_url, site_title, admin_user, admin_password, admin_email, port=None, ip_url=None):
    """Install WordPress via HTTPS domain (Cloudflare provides SSL).

    ip_url: if provided, use IP:port direct access for install steps
            (bypasses nginx reverse proxy which may not forward sub-paths correctly).
    """
    import sys as _sys
    def _log(msg):
        logger.info(msg)
        print(f"[WP] {msg}", file=_sys.stderr, flush=True)

    domain = site_url.replace("http://", "").replace("https://", "").rstrip("/")
    wp_url = f"http://{domain}"
    # Use IP:port direct access for install steps when available (nginx may not forward sub-paths)
    _install_url = ip_url or wp_url

    # ---- Phase 1: Wait for container to start (accept IP as proof of life) ----
    _log(f"waiting for container to start (checking {wp_url} with IP fallback)")
    container_up = False
    dns_ok = False
    for attempt in range(30):
        try:
            r = http_requests.get(f"{wp_url}/", timeout=10, allow_redirects=True)
            _log(f"readiness #{attempt+1}: status={r.status_code}")
            container_up = True
            dns_ok = True  # domain resolved successfully
            if r.status_code == 200:
                break
        except Exception as e:
            _log(f"readiness #{attempt+1}: {e}")
            if _install_url != wp_url and "NameResolutionError" in str(e):
                try:
                    r_ip = http_requests.get(f"{_install_url}/", timeout=10, allow_redirects=True)
                    _log(f"readiness #{attempt+1} via IP: status={r_ip.status_code}")
                    if r_ip.status_code == 200:
                        _log("container is up via IP — now waiting for DNS propagation...")
                        container_up = True
                        break
                except Exception:
                    pass
        time.sleep(5)
    if not container_up:
        return {"success": False, "message": "WordPress容器启动超时"}

    # ---- Phase 2: Wait for DNS to resolve (REQUIRED before proceeding) ----
    if not dns_ok:
        _log("DNS not resolved yet — waiting up to 10 minutes for propagation...")
        for dns_attempt in range(120):
            try:
                r = http_requests.get(f"{wp_url}/", timeout=10, allow_redirects=True)
                _log(f"DNS resolved after {dns_attempt*5+5}s! status={r.status_code}")
                dns_ok = True
                break
            except Exception:
                if dns_attempt % 12 == 0 and dns_attempt > 0:
                    _log(f"DNS still not resolving after {dns_attempt*5}s...")
                time.sleep(5)
        if not dns_ok:
            _log("DNS never resolved after 10 minutes — proceeding with IP fallback as last resort")
    else:
        time.sleep(3)  # brief pause to let nginx settle

    # ---- Phase 3: Install WordPress via domain (or IP as last resort) ----
    # Override _install_url: prefer domain if DNS resolved, else keep IP fallback
    _install_url = wp_url if dns_ok else _install_url
    _log(f"Installing via {_install_url}")

    # Already installed? (allow_redirects=False: 200=installed, 302=not installed)
    try:
        r = http_requests.get(f"{_install_url}/wp-login.php", timeout=10, allow_redirects=False)
        _log(f"check installed: wp-login status={r.status_code}")
        if r.status_code == 200:
            if _fix_wp_timeout(wp_url, admin_user, admin_password, ip_url=_install_url):
                _log("PHP timeout fix: MU plugin + .htaccess OK")
            _fix_wp_siteurl(_install_url, admin_user, admin_password, wp_url, _log)
            return {"success": True, "message": "WordPress已安装"}
    except Exception:
        pass

    # Install WordPress via HTTPS
    _log(f"POST install to {_install_url}/wp-admin/install.php?step=2")
    install_ok = False
    for submit_label in ["Install WordPress", "安装 WordPress"]:
        for attempt in range(3):
            try:
                r = http_requests.post(f"{_install_url}/wp-admin/install.php?step=2", data={
                    "weblog_title": site_title or domain,
                    "user_name": admin_user,
                    "admin_password": admin_password,
                    "admin_password2": admin_password,
                    "pw_weak": "1",
                    "admin_email": admin_email,
                    "language": "",
                    "Submit": submit_label,
                }, timeout=300, allow_redirects=True)
                _log(f"install POST #{attempt+1} [{submit_label}]: status={r.status_code}, len={len(r.text)}")
                if any(kw in r.text.lower() for kw in ["already installed", "success!", "wordpress has been installed"]):
                    _log("install success detected via text match")
                    install_ok = True; break
                if "wp-login" in r.url:
                    _log("install success detected via wp-login redirect")
                    install_ok = True; break
            except Exception as e:
                _log(f"install POST error: {e}")
            time.sleep(3)
        if install_ok:
            break

    if not install_ok:
        return {"success": False, "message": "WordPress安装失败"}

    # Verify (allow_redirects=False: 200=installed, 302=redirect to install=not ready)
    for va in range(10):
        try:
            r = http_requests.get(f"{_install_url}/wp-login.php", timeout=10, allow_redirects=False)
            _log(f"verify #{va+1}: wp-login status={r.status_code}")
            if r.status_code == 200:
                _log(f"INSTALL CONFIRMED after {(va+1)*3}s")
                # PHP timeout fix first — removes wp-config.php port defines
                wp_fixed = _fix_wp_timeout(wp_url, admin_user, admin_password, ip_url=_install_url)
                if wp_fixed:
                    _log("PHP timeout fix: MU plugin + .htaccess OK")
                # Fix siteurl/home in DB — must be AFTER wp-config defines are gone
                _fix_wp_siteurl(_install_url, admin_user, admin_password, wp_url, _log)
                # Post-install configs (language) are applied AFTER
                # plugins are installed, so the Cloudflare SSL plugin can fix
                # the redirect loop first.
                # Give Apache a moment to pick up .htaccess changes
                time.sleep(3)
                return {"success": True, "message": "WordPress安装成功"}
        except Exception:
            pass
        time.sleep(3)

    return {"success": False, "message": "安装验证超时"}

def _check_docker_socket() -> bool:
    """Test if Docker socket is accessible. Logs clearly if not."""
    sock = "/var/run/docker.sock"
    if not os.path.exists(sock):
        logger.warning("Docker socket NOT FOUND at %s — container timeout fix will not work", sock)
        return False
    try:
        import socket as _s
        s = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock)
        s.close()
        logger.info("Docker socket OK at %s", sock)
        return True
    except Exception as e:
        logger.warning("Docker socket %s not accessible: %s", sock, e)
        return False


def _do_docker_image_cleanup(site_id) -> None:
    """Delete ALL unused Docker images via socket. Run after container is removed."""
    sock = "/var/run/docker.sock"
    if not os.path.exists(sock):
        logger.warning("Docker socket not found at %s, skip image cleanup", sock)
        return

    try:
        import json as _json
        import socket as _socket
        import http.client as _hc

        def _sock_request(method, path, timeout=30):
            s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(sock)
            c = _hc.HTTPConnection("localhost")
            c.sock = s
            c.request(method, path)
            r = c.getresponse()
            data = _json.loads(r.read()) if r.status in (200, 204) and r.length else {}
            s.close()
            return data if isinstance(data, (list, dict)) else {}

        # 1. Get ALL images
        all_images = _sock_request("GET", "/images/json")
        if not all_images:
            logger.info("No Docker images found for cleanup")
            return

        # 2. Get ALL containers (including stopped) to find in-use images
        containers = _sock_request("GET", "/containers/json?all=true")
        in_use_ids = set()
        for ct in containers:
            img_id = ct.get("ImageID", "")
            if img_id:
                in_use_ids.add(img_id.split(":")[-1][:12] if ":" in img_id else img_id[:12])

        # 3. Delete images not used by any container
        deleted = 0
        for img in all_images:
            img_id = img.get("Id", "").split(":")[-1][:12]  # sha256:abc123... → first 12 chars
            if img_id in in_use_ids:
                continue
            tags = img.get("RepoTags") or []
            tag_name = tags[0] if tags and tags[0] != "<none>:<none>" else img_id
            try:
                s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                s.settimeout(15)
                s.connect(sock)
                c = _hc.HTTPConnection("localhost")
                c.sock = s
                c.request("DELETE", f"/images/{img.get('Id', '')}?force=true")
                r = c.getresponse()
                s.close()
                if r.status in (200, 204):
                    deleted += 1
                    logger.info("Deleted unused image: %s", tag_name)
            except Exception:
                pass

        if deleted:
            logger.info("Cleaned up %d unused Docker images after site %s deletion", deleted, site_id)
    except Exception as e:
        logger.warning("Docker image cleanup error: %s", e)


def _docker_list_containers(sock_path: str = "/var/run/docker.sock") -> list[dict]:
    """List all Docker containers (running + stopped) via Unix socket."""
    import json as _json
    import http.client as _hc
    import socket as _socket

    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(sock_path)
        conn = _hc.HTTPConnection("localhost")
        conn.sock = s
        conn.request("GET", "/containers/json?all=true")
        resp = conn.getresponse()
        data = resp.read()
        s.close()
        if resp.status == 200:
            return _json.loads(data)
    except Exception as e:
        logger.warning("docker list containers failed: %s", e)
    return []


def _docker_resolve_container_name(reported_name: str, alias: str = None) -> str:
    """Resolve a 1Panel-reported container name to a real Docker container name.

    1Panel may report names that differ from Docker's actual container names
    (e.g. Docker Compose project prefixes add `project_` before or `_1` after).

    Matching is strict to avoid confusing containers across multiple sites:
      1. Exact match
      2. Container name contains reported_name as a **segment** (delimited by
         `_`, `-`, or string boundaries) — catches Compose prefix/suffix
      3. If alias given, container name contains alias as a segment
    """
    if not reported_name:
        return ""

    def _segment_match(name: str, term: str) -> bool:
        """True if *term* appears in *name* as a delimited segment."""
        if not term:
            return False
        idx = name.find(term)
        if idx < 0:
            return False
        before_ok = idx == 0 or name[idx - 1] in ("_", "-", "/")
        after = idx + len(term)
        after_ok = after == len(name) or name[after] in ("_", "-")
        return before_ok and after_ok

    # Try exact match first
    _ec, _ = _docker_exec("/var/run/docker.sock", reported_name, ["echo", "ok"])
    if _ec == 0:
        return reported_name

    containers = _docker_list_containers()
    if not containers:
        return reported_name

    # Build candidate list: (name, score)
    # score: 2 = reported_name segment match, 1 = alias segment match
    candidates = []
    for c in containers:
        names = c.get("Names") or []
        state = c.get("State", "")
        for name in names:
            clean = name.lstrip("/")
            if _segment_match(clean, reported_name):
                candidates.append((clean, 2, state))
            elif alias and _segment_match(clean, alias):
                candidates.append((clean, 1, state))

    if len(candidates) == 1:
        name, score, state = candidates[0]
        logger.info("Resolved Docker container: %s -> %s (score=%s, state=%s)",
                    reported_name, name, score, state)
        return name

    if len(candidates) > 1:
        # Prefer running containers, then by score
        running = [(n, s) for n, s, st in candidates if st == "running"]
        if running:
            best = max(running, key=lambda x: x[1])
        else:
            best = max(candidates, key=lambda x: x[1])
        logger.warning("Multiple container candidates for '%s' (alias=%s): %s → picked %s",
                       reported_name, alias,
                       [(n, sc) for n, sc, _ in candidates], best[0])
        return best[0]

    # No segment match found — log all container names for debugging
    all_names = [n.lstrip("/") for c in containers for n in (c.get("Names") or [])]
    logger.warning("Could not resolve container name '%s' (alias=%s). Available: %s",
                   reported_name, alias, all_names)
    return reported_name


def _docker_exec(sock_path: str, container_name: str, cmd: list[str]) -> tuple[int, str]:
    """Run *cmd* inside *container_name* via Docker Unix socket. Returns (exit_code, stdout)."""
    import json as _json
    import http.client as _hc
    import socket as _socket

    # Connect via Unix socket
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect(sock_path)

    # Create exec instance
    body = _json.dumps({"AttachStdout": True, "AttachStderr": True, "Cmd": cmd})
    conn = _hc.HTTPConnection("localhost")
    conn.sock = s
    conn.request("POST", f"/containers/{container_name}/exec",
                 body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    if resp.status != 201:
        s.close()
        return -1, f"exec create failed: HTTP {resp.status}"
    exec_id = _json.loads(resp.read())["Id"]

    # Start exec
    s2 = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s2.settimeout(60)
    s2.connect(sock_path)
    conn2 = _hc.HTTPConnection("localhost")
    conn2.sock = s2
    conn2.request("POST", f"/exec/{exec_id}/start",
                  body=_json.dumps({"Detach": False, "Tty": False}),
                  headers={"Content-Type": "application/json"})
    resp2 = conn2.getresponse()
    raw = resp2.read()
    s2.close()

    # Docker multiplexes stdout/stderr — strip 8-byte headers
    output = b""
    i = 0
    while i < len(raw):
        if i + 8 > len(raw):
            break
        stream_type = raw[i]
        frame_len = int(raw[i+4:i+8].hex(), 16)
        i += 8
        if stream_type in (1, 2):  # stdout or stderr
            output += raw[i:i+frame_len]
        i += frame_len

    return 0, output.decode(errors="ignore").strip()


def _docker_cp_to_container(container_name, local_path, container_path):
    """Copy a file into a Docker container via the Docker Unix socket API.

    Uses PUT /containers/{id}/archive?path={dir} with a tar body.
    Returns True on success.
    """
    import tarfile as _tarfile
    import io as _io
    import socket as _socket
    import http.client as _hc

    if not container_name or not os.path.isfile(local_path):
        return False

    try:
        # Build tar archive in memory
        tar_buf = _io.BytesIO()
        with _tarfile.open(fileobj=tar_buf, mode='w') as tar:
            tar.add(local_path, arcname=os.path.basename(container_path))
        tar_buf.seek(0)
        tar_data = tar_buf.read()

        # PUT to Docker API
        dst_dir = os.path.dirname(container_path)
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.settimeout(30)
        s.connect("/var/run/docker.sock")
        conn = _hc.HTTPConnection("localhost")
        conn.sock = s
        url = f"/containers/{container_name}/archive?path={dst_dir}"
        conn.request("PUT", url, body=tar_data,
                     headers={"Content-Type": "application/x-tar"})
        resp = conn.getresponse()
        resp.read()
        s.close()
        return resp.status == 200
    except Exception as e:
        logger.warning("docker cp to %s failed: %s", container_name, e)
        return False



def _get_kairui_dbg_b64():
    """Return base64-encoded kairui-dbg.php content.

    kairui-dbg.php is a standalone PHP file that does NOT load WordPress.
    It parses wp-config.php directly to get DB credentials, then deactivates
    problematic plugins via PDO — so it works even when a fatal plugin error
    crashes the entire admin area.
    """
    import base64 as _b64
    php = (
        '<?php\n'
        '$s = isset($_GET["s"]) ? $_GET["s"] : "";\n'
        'if ($s !== "kairui_import_2024") { http_response_code(403); exit; }\n'
        'if (isset($_GET["fix"])) {\n'
        '  $fix = preg_replace("/[^a-z0-9_-]/", "", $_GET["fix"]);\n'
        '  // Try standard location first, then parent dir (1Panel secures wp-config.php one level up)\n'
        '  $cfg_file = __DIR__ . "/wp-config.php";\n'
        '  if (!file_exists($cfg_file)) {\n'
        '    $cfg_file = dirname(__DIR__) . "/wp-config.php";\n'
        '  }\n'
        '  if (!file_exists($cfg_file)) { echo "FAIL:no config at " . __DIR__; exit; }\n'
        '  $lines = file($cfg_file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);\n'
        '  function _kairui_extract($lines, $const) {\n'
        '    foreach ($lines as $l) {\n'
        '      $l = trim($l);\n'
        '      // Match define("CONST","val") or define(\'CONST\',\'val\') using hex escapes\n'
        '      if (preg_match(\'/define\\s*\\(\\s*[\\x27\\x22]\' . $const . \'[\\x27\\x22]\\s*,\\s*[\\x27\\x22]([^\\x27\\x22]*)[\\x27\\x22]/i\', $l, $m)) return $m[1];\n'
        '    }\n'
        '    return "";\n'
        '  }\n'
        '  $db   = _kairui_extract($lines, "DB_NAME");\n'
        '  $du   = _kairui_extract($lines, "DB_USER");\n'
        '  $dp   = _kairui_extract($lines, "DB_PASSWORD");\n'
        '  $dh   = _kairui_extract($lines, "DB_HOST"); if (!$dh) $dh = "localhost";\n'
        '  $pfx  = _kairui_extract($lines, "table_prefix"); if (!$pfx) $pfx = "wp_";\n'
        '  if (!$db) {\n'
        '    // Dump first 3 lines for debugging (mask password-like values)\n'
        '    $preview = array_slice($lines, 0, min(3, count($lines)));\n'
        '    echo "FAIL:no DB_NAME in " . basename($cfg_file) . " (lines=" . count($lines) . "): " . implode(" | ", $preview);\n'
        '    exit;\n'
        '  }\n'
        '  try {\n'
        '    $pdo = new PDO("mysql:host=$dh;dbname=$db;charset=utf8mb4", $du, $dp, [PDO::ATTR_TIMEOUT => 5, PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);\n'
        '    $st = $pdo->prepare("SELECT option_value FROM {$pfx}options WHERE option_name = ?");\n'
        '    $st->execute(["active_plugins"]);\n'
        '    $row = $st->fetch();\n'
        '    if (!$row) { echo "FAIL:active_plugins not found"; exit; }\n'
        '    $plugins = @unserialize($row["option_value"]);\n'
        '    if (!is_array($plugins)) { echo "FAIL:unserialize"; exit; }\n'
        '    $filtered = []; $removed = [];\n'
        '    foreach ($plugins as $p) {\n'
        '      if (strpos($p, $fix . "/") === 0 || $p === $fix . ".php") { $removed[] = $p; continue; }\n'
        '      $filtered[] = $p;\n'
        '    }\n'
        '    $new_val = serialize($filtered);\n'
        '    $upd = $pdo->prepare("UPDATE {$pfx}options SET option_value = ? WHERE option_name = ?");\n'
        '    $upd->execute([$new_val, "active_plugins"]);\n'
        '    echo "OK:removed " . count($removed) . " plugin(s): " . implode(", ", $removed);\n'
        '  } catch (Exception $e) { echo "FAIL:" . $e->getMessage(); }\n'
        '  exit;\n'
        '}\n'
        '$log = __DIR__ . "/wp-content/debug.log";\n'
        'if (file_exists($log)) {\n'
        '  if (isset($_GET["c"])) { @unlink($log); echo "cleared"; }\n'
        '  else { header("Content-Type: text/plain; charset=utf-8"); readfile($log); }\n'
        '} else { echo "(no debug.log)"; }\n'
    )
    return _b64.b64encode(php.encode()).decode()


def _fix_wp_timeout(wp_url, admin_user, admin_password, ip_url=None):
    """Install a must-use plugin to raise PHP execution limits.

    Two-step approach (same pattern as _php_activate_all_plugins):
    1. Upload a tiny plugin whose only job is to write an MU plugin file.
       This minimizes failure during activation — a simple file_put_contents
       is far less likely to error than running all the setup code inline.
    2. The MU plugin (persistent) does all the actual work on next page load:
       PHP limits, error capture, kairui-dbg.php, .user.ini, .htaccess.
    """
    import io
    import base64 as _b64
    import re as _re
    import zipfile

    _install_url = ip_url or wp_url

    try:
        s = http_requests.Session()

        # 1. Login
        r = s.post(f"{_install_url}/wp-login.php", data={
            "log": admin_user, "pwd": admin_password,
            "wp-submit": "Log In", "redirect_to": f"{_install_url}/wp-admin/",
        }, timeout=30, allow_redirects=False)
        if r.status_code != 302:
            logger.warning("wp-timeout: login returned %s", r.status_code)
            return False

        # 2. Get plugin upload nonce
        r2 = s.get(f"{_install_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
        up_nonce = _re.search(r'name="_wpnonce" value="([^"]+)"', r2.text)
        if not up_nonce:
            logger.warning("wp-timeout: upload nonce not found")
            return False

        # 3. Build MU plugin PHP (persistent: PHP limits + kairui-dbg.php + siteurl fix)
        #    wp-config.php port fix is handled by register_activation_hook below (one-time, no sentinel).
        _mu_php = (
            '<?php\n'
            '@set_time_limit(600);\n'
            '@ini_set("max_execution_time", "600");\n'
            '@ini_set("max_input_time", "600");\n'
            '@ini_set("max_input_vars", "10000");\n'
            '@ini_set("memory_limit", "1024M");\n'
            '@ini_set("display_errors", "0");\n'
            '@ini_set("error_reporting", (string)E_ALL);\n'
            'add_filter("wp_fatal_error_handler_enabled", "__return_false");\n'
            'register_shutdown_function(function() {\n'
            '  $e = error_get_last();\n'
            '  if ($e && in_array($e["type"], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR])) {\n'
            '    $msg = date("[Y-m-d H:i:s] ") . $e["message"] . " in " . $e["file"] . ":" . $e["line"] . "\\n";\n'
            '    @file_put_contents(WP_CONTENT_DIR . "/debug.log", $msg, FILE_APPEND);\n'
            '    @header("X-PHP-Fatal: " . substr($e["message"], 0, 200));\n'
            '  }\n'
            '});\n'
            '$reader = ABSPATH . "kairui-dbg.php";\n'
            '  file_put_contents($reader, base64_decode("'
            + _get_kairui_dbg_b64()
            + '"));\n'
            '// Fix siteurl/home — strip port + use HTTP_HOST domain (one-time)\n'
            '$fixed_key = "kairui_siteurl_fixed";\n'
            'if (!get_option($fixed_key)) {\n'
            '  $su = get_option("siteurl");\n'
            '  $ho = get_option("home");\n'
            '  // Strip port from existing URLs\n'
            '  $su = preg_replace("/:\\d+/", "", $su);\n'
            '  $ho = preg_replace("/:\\d+/", "", $ho);\n'
            '  // Use current request\'s host if it looks like a real domain (not an IP)\n'
            '  $host = $_SERVER["HTTP_HOST"] ?? "";\n'
            '  $host = preg_replace("/:\\d+$/", "", $host);\n'
            '  $is_domain = $host && !preg_match("/^\\d+\\.\\d+\\.\\d+\\.\\d+$/", $host);\n'
            '  if ($is_domain && strpos($su, "//" . $host) === false) {\n'
            '    $scheme = parse_url($su, PHP_URL_SCHEME) ?: "http";\n'
            '    $su = $scheme . "://" . $host;\n'
            '    $ho = $su;\n'
            '  }\n'
            '  update_option("siteurl", $su);\n'
            '  update_option("home", $ho);\n'
            '  // Only lock the fix when we actually got a domain name (not an IP)\n'
            '  // — IP-triggered runs only strip the port; domain-triggered runs fix the host.\n'
            '  if ($is_domain) {\n'
            '    update_option($fixed_key, 1);\n'
            '  }\n'
            '  @file_put_contents(WP_CONTENT_DIR . "/debug.log",\n'
            '    date("[Y-m-d H:i:s] ") . "mu-plugin: siteurl fixed to " . $su . "\\n",\n'
            '    FILE_APPEND);\n'
            '}\n'
            '$ui = ABSPATH . ".user.ini";\n'
            '$ui_content = file_exists($ui) ? file_get_contents($ui) : "";\n'
            'if (strpos($ui_content, "display_errors") === false) {\n'
            '  $ui_append = "\\nupload_max_filesize = 128M\\n"\n'
            '    . "post_max_size = 128M\\n"\n'
            '    . "max_execution_time = 600\\n"\n'
            '    . "max_input_time = 600\\n"\n'
            '    . "max_input_vars = 10000\\n"\n'
            '    . "memory_limit = 1024M\\n"\n'
            '    . "display_errors = On\\n"\n'
            '    . "error_reporting = E_ALL\\n";\n'
            '  file_put_contents($ui, $ui_content . $ui_append);\n'
            '}\n'
            '$ht = ABSPATH . ".htaccess";\n'
            '$ht_content = file_exists($ht) ? file_get_contents($ht) : "";\n'
            'if (strpos($ht_content, "display_errors") === false) {\n'
            '  $ht_append = "\\n<IfModule mod_php.c>\\n"\n'
            '    . "php_value max_execution_time 600\\n"\n'
            '    . "php_value max_input_time 600\\n"\n'
            '    . "php_value max_input_vars 10000\\n"\n'
            '    . "php_value upload_max_filesize 128M\\n"\n'
            '    . "php_value post_max_size 128M\\n"\n'
            '    . "php_value memory_limit 1024M\\n"\n'
            '    . "php_flag display_errors On\\n"\n'
            '    . "php_value error_reporting -1\\n"\n'
            '    . "</IfModule>\\n";\n'
            '  file_put_contents($ht, $ht_content . $ht_append);\n'
            '}\n'
        )
        _mu_b64 = _b64.b64encode(_mu_php.encode()).decode()

        # 4. Build tiny plugin zip:
        #    a) register_activation_hook: fix wp-config.php port defines (one-time, no sentinel)
        #    b) Writes persistent MU plugin for PHP limits
        #    c) Self-destructs on shutdown
        _plugin_code = (
            '<?php\n'
            '/* Plugin Name: WP Timeout Fix */\n'
            '// Fix wp-config.php on activation — 1Panel may add WP_SITEURL/WP_HOME with container port\n'
            'register_activation_hook(__FILE__, function() {\n'
            '  $cfg_file = ABSPATH . "wp-config.php";\n'
            '  $cfg = @file_get_contents($cfg_file);\n'
            '  if ($cfg) {\n'
            '    $lines = explode("\\n", $cfg);\n'
            '    $out = []; $changed = false;\n'
            '    foreach ($lines as $line) {\n'
            '      $t = trim($line);\n'
            '      if (\n'
            '        (strpos($t, "WP_SITEURL") !== false || strpos($t, "WP_HOME") !== false)\n'
            '        && strpos($t, "define") !== false\n'
            '        && preg_match("/:\\d+/", $t)\n'
            '      ) {\n'
            '        $changed = true; continue;\n'
            '      }\n'
            '      $out[] = $line;\n'
            '    }\n'
            '    if ($changed) {\n'
            '      @file_put_contents($cfg_file, implode("\\n", $out));\n'
            '      @file_put_contents(WP_CONTENT_DIR . "/debug.log",\n'
            '        date("[Y-m-d H:i:s] ") . "wp-config-fix: removed WP_SITEURL/WP_HOME with port\\n",\n'
            '        FILE_APPEND);\n'
            '    }\n'
            '  }\n'
            '});\n'
            '// Write persistent MU plugin for PHP limits\n'
            '$mu = rtrim(WP_CONTENT_DIR, "/") . "/mu-plugins";\n'
            'if (!is_dir($mu)) @mkdir($mu, 0755, true);\n'
            f'file_put_contents($mu . "/wp-timeout-fix.php", base64_decode("{_mu_b64}"));\n'
            '// Self-destruct on shutdown\n'
            'add_action("shutdown", function() {\n'
            '  $active = get_option("active_plugins", []);\n'
            '  if (is_array($active)) {\n'
            '    $active = array_values(array_filter($active, function($p) {\n'
            '      return strpos($p, "wp-timeout-fix/") !== 0;\n'
            '    }));\n'
            '    update_option("active_plugins", $active);\n'
            '  }\n'
            '  $d = WP_PLUGIN_DIR . "/wp-timeout-fix";\n'
            '  $fs = @glob($d . "/*"); if ($fs) { foreach ($fs as $f) @unlink($f); }\n'
            '  @rmdir($d);\n'
            '});\n'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("wp-timeout-fix/wp-timeout-fix.php", _plugin_code)
        buf.seek(0)

        # 5. Upload plugin
        r3 = s.post(
            f"{_install_url}/wp-admin/update.php?action=upload-plugin",
            files={"pluginzip": ("wp-timeout-fix.zip", buf, "application/zip")},
            data={"_wpnonce": up_nonce.group(1)},
            timeout=60,
            allow_redirects=True,
        )
        logger.info("wp-timeout: uploaded HTTP %s, URL=%s", r3.status_code, r3.url[:120])

        # 6. Activate plugin → register_activation_hook fires → wp-config.php fixed + MU plugin written
        time.sleep(1)
        r4 = s.get(f"{_install_url}/wp-admin/plugins.php", timeout=15)
        activate_url = None
        for pat in [
            r'plugins\.php\?action=activate&(?:amp;)?plugin=wp-timeout-fix[^"\']+',
            r'action=activate&(?:amp;)?plugin=wp-timeout-fix[^"\']+',
        ]:
            m = _re.search(pat, r4.text)
            if m:
                activate_url = m.group(0).replace("&amp;", "&")
                break
        if not activate_url:
            nonce_m = _re.search(r'name="_wpnonce"\s+value="([^"]+)"', r4.text)
            wp_nonce = _re.search(r'id="_wpnonce"\s+name="_wpnonce"\s+value="([^"]+)"', r4.text)
            bulk_nonce = (nonce_m or wp_nonce)
            if bulk_nonce:
                activate_url = f"action=activate&plugin=wp-timeout-fix%2Fwp-timeout-fix.php&_wpnonce={bulk_nonce.group(1)}"

        if not activate_url:
            logger.warning("wp-timeout: activate link not found on plugins page")
            s.close()
            return False

        if activate_url.startswith("plugins.php"):
            activate_full = f"{_install_url}/wp-admin/{activate_url}"
        else:
            activate_full = f"{_install_url}/wp-admin/plugins.php?{activate_url}"
        r5 = s.get(activate_full, timeout=15, allow_redirects=True)
        logger.info("wp-timeout: activate HTTP %s (wp-config fix + MU plugin written)", r5.status_code)

        # 7. Trigger MU plugin via DOMAIN first (so HTTP_HOST is domain, siteurl gets fixed correctly)
        #    The IP:port session is closed — use a fresh request to the domain URL.
        s.close()
        time.sleep(1)
        try:
            r_domain = http_requests.get(f"{wp_url}/", timeout=15)
            logger.info("wp-timeout: domain trigger %s → HTTP %s (MU plugin runs, siteurl fixed)",
                        wp_url, r_domain.status_code)
        except Exception as e:
            logger.warning("wp-timeout: domain trigger failed for %s: %s", wp_url, e)
            return False

        # 8. Verify kairui-dbg.php exists (MU plugin wrote it during domain trigger above)
        time.sleep(0.5)
        r_v = http_requests.get(f"{wp_url}/kairui-dbg.php?s=kairui_import_2024", timeout=10)
        if r_v.status_code == 200:
            logger.info("wp-timeout: verified — kairui-dbg.php exists, MU plugin is active")
            return True

        logger.warning("wp-timeout: kairui-dbg.php not reachable (HTTP %s, body_len=%d)",
                       r_v.status_code, len(r_v.text))
        return False
    except Exception as e:
        logger.warning("wp-timeout: %s", e)
        return False


def _php_activate_all_plugins(session, wp_url):
    """Activate ALL non-active plugins via a self-deleting MU-plugin approach.

    Instead of a plugin that does work during activation (which can 500),
    we upload a tiny plugin that only writes a mu-plugin file. The actual
    bulk activation runs as an MU plugin on the next page load — MU plugins
    load before regular plugins, so broken plugin code can't interfere.
    """
    import io, base64 as _b64, zipfile as _zipfile

    # Inner MU plugin code (base64-encoded to avoid PHP string escaping bugs)
    # NOTE: When this runs as MU plugin, the updated active_plugins IS picked up
    # in the same request (regular plugins load after MU plugins). Only call this
    # when plugins are known-stable — NOT during initial deployment.
    _mu_bulk_code = (
        '<?php\n'
        '/* Bulk activate all plugins — writes option then self-deletes */\n'
        '$active = get_option("active_plugins", []);\n'
        'if (!is_array($active)) $active = [];\n'
        '$added = 0;\n'
        '$dirs = @glob(WP_PLUGIN_DIR . "/*", GLOB_ONLYDIR);\n'
        'if ($dirs) {\n'
        '    foreach ($dirs as $d) {\n'
        '        $slug = basename($d);\n'
        '        if (!preg_match("/^[a-z0-9][a-z0-9_.-]*$/", $slug)) continue;\n'
        '        $fs = @glob("$d/*.php");\n'
        '        if (!$fs) continue;\n'
        '        $p = basename($d) . "/" . basename($fs[0]);\n'
        '        if (!in_array($p, $active)) { $active[] = $p; $added++; }\n'
        '    }\n'
        '}\n'
        'update_option("active_plugins", array_unique($active));\n'
        '@file_put_contents(WP_CONTENT_DIR . "/debug.log",\n'
        '    date("[Y-m-d H:i:s] ") . "bulk-activate: added $added plugins, total " . count($active) . "\n",\n'
        '    FILE_APPEND);\n'
        '// Self-destruct\n'
        '@unlink(__FILE__);\n'
    )
    _mu_bulk_b64 = _b64.b64encode(_mu_bulk_code.encode()).decode()

    # Tiny plugin: just writes a mu-plugin file, then self-destructs
    php_code = (
        '<?php\n'
        '/* Plugin Name: Bulk Activate MU Creator */\n'
        '$mu = rtrim(WP_CONTENT_DIR, "/") . "/mu-plugins/bulk-activate-plugins.php";\n'
        f'if (!file_exists($mu)) file_put_contents($mu, base64_decode("{_mu_bulk_b64}"));\n'
        '// Self-destruct (direct option manipulation — no deactivate_plugins dependency)\n'
        'add_action("shutdown", function() {\n'
        '    $active = get_option("active_plugins", []);\n'
        '    if (is_array($active)) {\n'
        '        $active = array_values(array_filter($active, function($p) {\n'
        '            return strpos($p, "bulk-activate-mu/") !== 0;\n'
        '        }));\n'
        '        update_option("active_plugins", $active);\n'
        '    }\n'
        '    $d = WP_PLUGIN_DIR . "/bulk-activate-mu";\n'
        '    $fs = @glob($d . "/*"); if ($fs) { foreach ($fs as $f) @unlink($f); }\n'
        '    @rmdir($d);\n'
        '});\n'
    )

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bulk-activate-mu/bulk-activate-mu.php", php_code)
    buf.seek(0)

    # Retry loop
    for attempt in range(3):
        try:
            r_nonce = session.get(f"{wp_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
            up_nonce = re.search(r'name="_wpnonce" value="([^"]+)"', r_nonce.text)
            if not up_nonce:
                logger.warning("php-activate: upload nonce not found (attempt %d)", attempt + 1)
                # Server might be responding 500 — wait longer
                time.sleep(2 * (attempt + 1))
                continue
            buf.seek(0)

            r_up = session.post(
                f"{wp_url}/wp-admin/update.php?action=upload-plugin",
                files={"pluginzip": ("bulk-activate-mu.zip", buf, "application/zip")},
                data={"_wpnonce": up_nonce.group(1)},
                timeout=60, allow_redirects=True,
            )
            logger.info("php-activate: uploaded HTTP %s (attempt %d)", r_up.status_code, attempt + 1)

            # Find activation link
            act_match = re.search(
                r'action=activate[^"]*plugin=bulk-activate-mu[^"]*_wpnonce=([a-f0-9]+)',
                r_up.text,
            )
            if not act_match:
                r_pl = session.get(f"{wp_url}/wp-admin/plugins.php", timeout=15)
                act_match = re.search(
                    r'action=activate[^"]*plugin=bulk-activate-mu[^"]*_wpnonce=([a-f0-9]+)',
                    r_pl.text,
                )
            if not act_match:
                logger.warning("php-activate: activation link not found (attempt %d)", attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue

            act_url = act_match.group(0).replace("&amp;", "&")
            r_act = session.get(
                f"{wp_url}/wp-admin/plugins.php?{act_url}",
                timeout=30, allow_redirects=True,
            )
            logger.info("php-activate: MU creator activated (HTTP %s, attempt %d)", r_act.status_code, attempt + 1)
            if r_act.status_code == 200:
                # MU plugin file was written. Visit home page to trigger it
                # (MU plugins auto-load, no activation needed)
                r_home = session.get(f"{wp_url}/", timeout=30)
                logger.info("php-activate: triggered via homepage (HTTP %s)", r_home.status_code)
                # Visit again to ensure all plugins are activated (first visit runs MU)
                session.get(f"{wp_url}/", timeout=30)
                return
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            logger.warning("php-activate: error (attempt %d): %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))

    logger.warning("php-activate: all 3 attempts failed")

def _install_wp_org_plugin(session, wp_url, slug):
    """Install and activate a plugin from WordPress.org plugin store.

    Downloads the zip from wp.org and uploads via the plugin upload form,
    bypassing the JavaScript-driven install-plugin nonce system entirely.
    Skips download if plugin is already installed (present on plugins.php page).
    """
    import io, zipfile as _zipfile
    logger.info("wp-org-install: installing %s from WP.org", slug)
    try:
        # Quick pre-check: if plugin directory already exists on plugins.php, skip download.
        # Use data-plugin (real path e.g. "google-listings-and-ads/xxx.php") not data-slug
        # which may differ (WP uses "google-for-woocommerce" as display slug).
        r_pl_check = session.get(f"{wp_url}/wp-admin/plugins.php", timeout=15)
        if f'data-plugin="{slug}/' in r_pl_check.text:
            logger.info("wp-org-install: %s already installed, skipping download", slug)
            return {"success": True, "message": f"{slug} already installed"}

        # Download the plugin zip from WordPress.org
        dl_url = f"https://downloads.wordpress.org/plugin/{slug}.latest-stable.zip"
        r_dl = http_requests.get(dl_url, timeout=120, allow_redirects=True)
        if r_dl.status_code != 200 or len(r_dl.content) < 100:
            logger.warning("wp-org-install: %s download failed (HTTP %s, len=%s)", slug, r_dl.status_code, len(r_dl.content))
            return {"success": False, "message": f"Download failed: HTTP {r_dl.status_code}"}
        logger.info("wp-org-install: downloaded %s (%d bytes)", slug, len(r_dl.content))

        # Upload via standard plugin upload form (nonce from plugin-install.php?tab=upload)
        r_nonce = session.get(f"{wp_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
        up_nonce = re.search(r'name="_wpnonce" value="([^"]+)"', r_nonce.text)
        if not up_nonce:
            return {"success": False, "message": "Upload nonce not found"}

        r_up = session.post(
            f"{wp_url}/wp-admin/update.php?action=upload-plugin",
            files={"pluginzip": (f"{slug}.zip", io.BytesIO(r_dl.content), "application/zip")},
            data={"_wpnonce": up_nonce.group(1)},
            timeout=180, allow_redirects=True,
        )
        logger.info("wp-org-install: %s install -> HTTP %s", slug, r_up.status_code)

        # Activate plugin (no sleep needed — WP processes upload synchronously)
        r_pl = session.get(f"{wp_url}/wp-admin/plugins.php", timeout=15)
        act_match = re.search(
            rf'action=activate[^"]*plugin={re.escape(slug)}[^"]*_wpnonce=([a-f0-9]+)',
            r_pl.text,
        )
        if act_match:
            act_url = act_match.group(0).replace("&amp;", "&")
            r_act = session.get(f"{wp_url}/wp-admin/plugins.php?{act_url}", timeout=30, allow_redirects=True)
            logger.info("wp-org-install: %s activated (HTTP %s)", slug, r_act.status_code)
            return {"success": True, "message": f"{slug} installed and activated"}
        logger.info("wp-org-install: %s activate link not found (may already be active)", slug)
        return {"success": True, "message": f"{slug} already installed / activate link not found"}
    except Exception as _e:
        logger.warning("wp-org-install: %s error: %s", slug, _e)
        return {"success": False, "message": str(_e)[:200]}


def _get_internal_backend_url():
    """返回 WP 容器可访问的后端 URL（同机部署时用于加速插件下载）。

    仅当 BACKEND_HOST (显式) 或 DOCKER_HOST_IP 环境变量设置时返回有效 URL。
    跨机器部署时返回空字符串，MU 插件自动回退到 wp.org 下载。
    """
    port = os.environ.get("WP_PORT", "8011")

    # 1) Explicit backend host
    explicit = os.environ.get("BACKEND_HOST", "")
    if explicit:
        return f"http://{explicit}:{port}"

    # 2) Docker host gateway (同机部署)
    server_ip = os.environ.get("DOCKER_HOST_IP", "")
    if server_ip:
        return f"http://{server_ip}:{port}"

    # 3) 跨机器部署 — 不可达，MU 插件将使用 wp.org
    return ""


def _apply_post_install_configs(wp_url, admin_user, admin_password, theme_slug, ip_url=None):
    """Upload a self-deleting plugin that installs all plugins from theme inc/plugins/.

    All plugin zips (woodmart-core, woocommerce, woodmart-images-optimizer, etc.)
    are pre-injected into the theme's inc/plugins/ by the deploy flow before upload.
    This function only needs to: unzip → activate. No downloads, no tiered fallbacks.

    1. Creates self-deleting MU plugin to unzip + activate all inc/plugins/*.zip
    2. Sets admin user locale to zh_CN
    3. Visits wp-admin to trigger auto-install, polls until complete
    4. The delivery plugin and the MU plugin both self-destruct
    """
    import base64
    import io
    import re as _re
    import zipfile

    # MU plugin PHP: unzip all zips from theme inc/plugins/, then activate everything
    _auto_install_php = (
        '<?php\n'
        '@set_time_limit(300);\n'
        "@ini_set('memory_limit', '512M');\n"
        "add_action('admin_init', function() {\n"
        "    $log = WP_CONTENT_DIR . '/debug.log';\n"
        "    file_put_contents($log, date('[Y-m-d H:i:s] ') . \"auto-install MU plugin started\\n\", FILE_APPEND);\n"
        "    try {\n"
        "        $t0 = microtime(true);\n"
        "        // Delete Hello Dolly + Akismet (unwanted pre-installed plugins)\n"
        "        $hello = WP_PLUGIN_DIR . '/hello.php';\n"
        "        if (file_exists($hello)) @unlink($hello);\n"
        "        $akismet_dir = WP_PLUGIN_DIR . '/akismet';\n"
        "        if (is_dir($akismet_dir)) {\n"
        "            $files = new RecursiveIteratorIterator(\n"
        "                new RecursiveDirectoryIterator($akismet_dir, RecursiveDirectoryIterator::SKIP_DOTS),\n"
        "                RecursiveIteratorIterator::CHILD_FIRST\n"
        "            );\n"
        "            foreach ($files as $f) {\n"
        "                $f->isDir() ? @rmdir($f->getRealPath()) : @unlink($f->getRealPath());\n"
        "            }\n"
        "            @rmdir($akismet_dir);\n"
        '            file_put_contents($log, "auto-install: akismet deleted\\n", FILE_APPEND);\n'
        "        }\n"
        "        require_once ABSPATH . 'wp-admin/includes/plugin.php';\n"
        "        require_once ABSPATH . 'wp-admin/includes/file.php';\n"
        "        WP_Filesystem();\n"
        "        // Install all plugins from theme inc/plugins/ (pre-injected by deploy flow)\n"
        "        $plugin_dir = WP_CONTENT_DIR . '/themes/K_THEME_SLUG/inc/plugins';\n"
        "        $zips = glob($plugin_dir . '/*.zip');\n"
        "        if ($zips) {\n"
        "            foreach ($zips as $zip) {\n"
        "                $slug = basename($zip, '.zip');\n"
        "                if (is_dir(WP_PLUGIN_DIR . '/' . $slug)) continue;\n"
        "                $unzipped = unzip_file($zip, WP_PLUGIN_DIR);\n"
        "                if (is_wp_error($unzipped)) {\n"
        '                    file_put_contents($log, "auto-install: $slug unzip ERROR: " . $unzipped->get_error_message() . "\\n", FILE_APPEND);\n'
        "                } else {\n"
        '                    file_put_contents($log, "auto-install: $slug unzipped OK\\n", FILE_APPEND);\n'
        "                }\n"
        "            }\n"
        "        }\n"
        "        // Activate all installed plugins (woocommerce first for dependency order)\n"
        "        if (!function_exists('get_plugins')) require_once ABSPATH . 'wp-admin/includes/plugin.php';\n"
        "        // Clear cached plugin list — get_plugins() may have been called before unzip\n"
        "        wp_cache_delete('plugins', 'plugins');\n"
        "        $all_plugins = get_plugins();\n"
        "        $slug_to_file = [];\n"
        "        foreach ($all_plugins as $pf => $pd) {\n"
        "            $s = dirname($pf);\n"
        "            if ($s !== '.' && $s !== '') $slug_to_file[$s] = $pf;\n"
        "        }\n"
        "        $active = get_option('active_plugins', []);\n"
        "        if (!is_array($active)) $active = [];\n"
        "        $dirs = glob(WP_PLUGIN_DIR . '/*', GLOB_ONLYDIR);\n"
        "        $to_activate = [];\n"
        "        if ($dirs) {\n"
        "            foreach ($dirs as $d) {\n"
        "                $slug = basename($d);\n"
        "                if (!preg_match('/^[a-z0-9][a-z0-9_.-]*$/', $slug)) continue;\n"
        "                if (isset($slug_to_file[$slug]) && !in_array($slug_to_file[$slug], $active)) {\n"
        "                    $to_activate[] = $slug_to_file[$slug];\n"
        "                }\n"
        "            }\n"
        "        }\n"
        '        file_put_contents($log, "auto-install: " . count($to_activate) . " plugins to activate\\n", FILE_APPEND);\n'
        "        // Prevent WC newly_installed fatal: set to 'no' so the hook\n"
        "        // (which calls wc_set_hooked_blocks_version) never fires.\n"
        "        if (isset($slug_to_file['woocommerce'])) {\n"
        "            update_option('woocommerce_newly_installed', 'no');\n"
        "        }\n"
        "        // WooCommerce first (dependency for others)\n"
        "        usort($to_activate, function($a, $b) {\n"
        "            $a_wc = (strpos($a, 'woocommerce/') === 0);\n"
        "            $b_wc = (strpos($b, 'woocommerce/') === 0);\n"
        "            if ($a_wc && !$b_wc) return -1;\n"
        "            if (!$a_wc && $b_wc) return 1;\n"
        "            return 0;\n"
        "        });\n"
        "        foreach ($to_activate as $p) {\n"
        "            $r = activate_plugin($p);\n"
        "            if (is_wp_error($r)) {\n"
        '                file_put_contents($log, "activate $p ERROR: " . $r->get_error_message() . "\\n", FILE_APPEND);\n'
        "            } else {\n"
        '                file_put_contents($log, "activate $p OK\\n", FILE_APPEND);\n'
        "            }\n"
        "        }\n"
        "        $elapsed = round(microtime(true) - $t0, 1);\n"
        '        file_put_contents($log, "auto-install: done in {$elapsed}s\\n", FILE_APPEND);\n'
        "    } catch (\\Throwable $e) {\n"
        '        file_put_contents($log, "auto-install FATAL: " . $e->getMessage() . "\\n", FILE_APPEND);\n'
        "    }\n"
        "    @unlink(__FILE__);\n"
        "}, 1);\n"
    ).replace("K_THEME_SLUG", theme_slug)
    _ai_b64 = base64.b64encode(_auto_install_php.encode("utf-8")).decode("ascii")
    _install_url = ip_url or wp_url

    try:
        s = http_requests.Session()

        # 1. Login
        r = s.post(f"{_install_url}/wp-login.php", data={
            "log": admin_user, "pwd": admin_password,
            "wp-submit": "Log In", "redirect_to": f"{_install_url}/wp-admin/",
        }, timeout=30, allow_redirects=False)
        if r.status_code not in (302, 200):
            logger.warning("post-config: login returned %s", r.status_code)
            return False

        # 2. Get plugin upload nonce
        r2 = s.get(f"{_install_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
        up_nonce = _re.search(r'name="_wpnonce" value="([^"]+)"', r2.text)
        if not up_nonce:
            logger.warning("post-config: upload nonce not found")
            s.close()
            return False

        # 3. Build delivery plugin zip — writes MU plugin, footer options, admin locale, then self-destructs
        plugin_code = (
            '<?php\n'
            '/* Plugin Name: WP Post-Install Config */\n'
            '$dlog = WP_CONTENT_DIR . "/debug.log";\n'
            '@file_put_contents($dlog, date("[Y-m-d H:i:s] ") . "post-config: delivery plugin activated\n", FILE_APPEND);\n'
            '$mu = rtrim(WP_CONTENT_DIR ?? ABSPATH . "wp-content", "/") . "/mu-plugins";\n'
            'if (!is_dir($mu)) { @mkdir($mu, 0755, true); }\n'

            # ---- Self-deleting MU plugin: unzip + activate all inc/plugins ----
            '$ai_file = $mu . "/auto-install-plugins.php";\n'
            'if (!file_exists($ai_file)) {\n'
            f'  $ai_code = base64_decode("{_ai_b64}");\n'
            '  file_put_contents($ai_file, $ai_code);\n'
            '  @file_put_contents($dlog, date("[Y-m-d H:i:s] ") . "post-config: wrote auto-install-plugins.php (" . strlen($ai_code) . " bytes)\n", FILE_APPEND);\n'
            '}\n'

            # ---- Footer options MU plugin ----
            '$footer_code = $mu . "/footer-options.php";\n'
            'if (!file_exists($footer_code)) {\n'
            '  file_put_contents($footer_code, \'<?php\n'
            'add_action("init", function() {\n'
            '    foreach (["wp_footer_address", "wp_footer_phone", "wp_footer_email", "wp_footer_logo"] as $opt) {\n'
            '        register_setting("general", $opt, ["type" => "string", "show_in_rest" => true, "default" => ""]);\n'
            '    }\n'
            '});\n'
            '\');\n'
            '  @file_put_contents($dlog, date("[Y-m-d H:i:s] ") . "post-config: wrote footer-options.php\n", FILE_APPEND);\n'
            '}\n'

            # ---- Set admin locale to zh_CN ----
            'add_action("init", function() {\n'
            '    $admin_user = wp_get_current_user();\n'
            '    if ($admin_user && $admin_user->ID) {\n'
            '        update_user_meta($admin_user->ID, "locale", "zh_CN");\n'
            '        @file_put_contents(WP_CONTENT_DIR . "/debug.log", date("[Y-m-d H:i:s] ") . "post-config: set locale=zh_CN for user " . $admin_user->ID . "\n", FILE_APPEND);\n'
            '    }\n'
            '});\n'

            # ---- Self-destruct ----
            'add_action("shutdown", function() {\n'
            '    $active = get_option("active_plugins", []);\n'
            '    if (is_array($active)) {\n'
            '        $active = array_values(array_filter($active, function($p) {\n'
            '            return strpos($p, "wp-postinstall-config/") !== 0;\n'
            '        }));\n'
            '        update_option("active_plugins", $active);\n'
            '    }\n'
            '    $dir = WP_PLUGIN_DIR . "/wp-postinstall-config";\n'
            '    $files = @glob($dir . "/*");\n'
            '    if ($files) { foreach ($files as $f) { @unlink($f); } }\n'
            '    @rmdir($dir);\n'
            '    @file_put_contents(WP_CONTENT_DIR . "/debug.log", date("[Y-m-d H:i:s] ") . "post-config: self-destruct done\n", FILE_APPEND);\n'
            '});\n'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("wp-postinstall-config/wp-postinstall-config.php", plugin_code)
        buf.seek(0)

        # 4. Upload plugin
        r3 = s.post(
            f"{_install_url}/wp-admin/update.php?action=upload-plugin",
            files={"pluginzip": ("wp-postinstall-config.zip", buf, "application/zip")},
            data={"_wpnonce": up_nonce.group(1)},
            timeout=60,
            allow_redirects=True,
        )
        logger.info("post-config: uploaded HTTP %s", r3.status_code)

        # 5. Activate → MU plugins created
        time.sleep(1)
        r4 = s.get(f"{_install_url}/wp-admin/plugins.php", timeout=15)
        activate_url = None
        for pat in [
            r'plugins\.php\?action=activate&(?:amp;)?plugin=wp-postinstall-config[^"\']+',
            r'action=activate&(?:amp;)?plugin=wp-postinstall-config[^"\']+',
        ]:
            m = _re.search(pat, r4.text)
            if m:
                activate_url = m.group(0).replace("&amp;", "&")
                break
        if not activate_url:
            nonce_m = _re.search(r'name="_wpnonce"\s+value="([^"]+)"', r4.text)
            if nonce_m:
                activate_url = f"action=activate&plugin=wp-postinstall-config%2Fwp-postinstall-config.php&_wpnonce={nonce_m.group(1)}"

        if activate_url:
            if activate_url.startswith("plugins.php"):
                activate_full = f"{_install_url}/wp-admin/{activate_url}"
            else:
                activate_full = f"{_install_url}/wp-admin/plugins.php?{activate_url}"
            r5 = s.get(activate_full, timeout=30, allow_redirects=True)
            logger.info("post-config: activated HTTP %s — MU plugin written", r5.status_code)
        else:
            logger.warning("post-config: activate link not found")

        # 6. Trigger MU plugin → unzips inc/plugins + activates all
        logger.info("post-config: triggering auto-install MU plugin")
        for _ai_poll in range(8):  # up to ~24s for large zips
            time.sleep(3)
            _ai_r = s.get(f"{_install_url}/wp-admin/", timeout=60, allow_redirects=True)
            if _ai_r.status_code != 200:
                continue
            _pl_r = s.get(f"{_install_url}/wp-admin/plugins.php", timeout=30)
            _slugs = _re.findall(r'data-slug="([^"]+)"', _pl_r.text)
            _real_slugs = [s for s in _slugs if _re.match(r'^[a-z0-9][a-z0-9_.-]*$', s)]
            _active = len(_re.findall(r'class="[^"]*\bactive\b', _pl_r.text))
            logger.info("post-config: auto-install poll #%d: %d plugins, %d active (slugs: %s)",
                        _ai_poll + 1, len(_slugs), _active, ", ".join(_slugs[:12]))
            if len(_real_slugs) >= 3:
                break

        # Read diagnostic log
        try:
            r_dbg = s.get(f"{_install_url}/kairui-dbg.php?s=kairui_import_2024", timeout=10)
            if r_dbg.status_code == 200 and r_dbg.text.strip() and r_dbg.text.strip() != "(no debug.log)":
                logger.info("post-config: auto-install diag:\n%s", r_dbg.text[:3000])
        except Exception:
            pass

        s.close()
        return True
    except Exception as e:
        logger.warning("post-config: %s", e)
        return False

def _install_ssl_plugin(wp_url, admin_user, admin_password):
    """Install and activate Cloudflare Flexible SSL plugin.

    Tries local backend/plugins/cloudflare-flexible-ssl.zip first,
    falls back to WordPress.org download.
    Returns True on success, False if skipped or failed.
    """
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")
    ssl_zip = os.path.join(plugins_dir, "cloudflare-flexible-ssl.zip")

    # Retry loop for DNS propagation / transient network issues
    for attempt in range(3):
        s = http_requests.Session()
        try:
            r = s.post(f"{wp_url}/wp-login.php", data={
                "log": admin_user, "pwd": admin_password,
                "wp-submit": "Log In", "redirect_to": f"{wp_url}/wp-admin/",
            }, timeout=30, allow_redirects=False)
            if r.status_code not in (302, 200):
                logger.warning("ssl-plugin: login failed HTTP %s (attempt %d/3)", r.status_code, attempt + 1)
                if attempt < 2:
                    time.sleep(10)
                    continue
                return False

            if os.path.isfile(ssl_zip):
                # Fast path: upload local zip
                logger.info("ssl-plugin: installing from local zip")
                r2 = s.get(f"{wp_url}/wp-admin/plugin-install.php?tab=upload", timeout=15)
                un_m = re.search(r'name="_wpnonce" value="([^"]+)"', r2.text)
                if not un_m:
                    logger.warning("ssl-plugin: nonce not found")
                    return False

                with open(ssl_zip, "rb") as f:
                    r_up = s.post(
                        f"{wp_url}/wp-admin/update.php?action=upload-plugin",
                        files={"pluginzip": ("cloudflare-flexible-ssl.zip", f, "application/zip")},
                        data={"_wpnonce": un_m.group(1)},
                        timeout=120, allow_redirects=True,
                    )
                logger.info("ssl-plugin: upload HTTP %s", r_up.status_code)

                # Activate
                time.sleep(2)
                r_pl = s.get(f"{wp_url}/wp-admin/plugins.php", timeout=15)
                act_url = None
                act_m = re.search(
                    r'action=activate[^"]*plugin=(cloudflare-flexible-ssl[^"&]*)[^"]*_wpnonce=([a-f0-9]+)',
                    r_pl.text,
                )
                if act_m:
                    act_url = f"action=activate&plugin={act_m.group(1)}&_wpnonce={act_m.group(2)}"
                if not act_url:
                    nm = re.search(r'name="_wpnonce"\s+value="([^"]+)"', r_pl.text)
                    if nm:
                        act_url = "action=activate&plugin=cloudflare-flexible-ssl%2Fplugin.php&_wpnonce=" + nm.group(1)

                if act_url:
                    r_act = s.get(f"{wp_url}/wp-admin/plugins.php?{act_url}", timeout=30, allow_redirects=True)
                    ok = r_act.status_code == 200
                    logger.info("ssl-plugin: activate=%s", ok)
                    return ok
                else:
                    logger.warning("ssl-plugin: activate URL not found")
                    return False
            else:
                # Fallback: download from WordPress.org
                logger.info("ssl-plugin: local zip not found, downloading from WP.org for %s", wp_url)
                result = _install_wp_org_plugin(s, wp_url, "cloudflare-flexible-ssl")
                return result.get("success", False)
        except Exception as e:
            logger.warning("ssl-plugin: attempt %d/3 error: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(10)
                continue
            return False
        finally:
            s.close()


def _nginx_fix_body_size(pc, alias, website_id=None):
    """Add client_max_body_size 128m to nginx config for large theme/plugin uploads.

    Tries multiple known paths where 1Panel/OpenResty stores per-site nginx configs.
    """
    body_directive = "    client_max_body_size 128m;\n"
    tried_paths = []

    # Path 1: Standard 1Panel nginx config for websites
    if website_id:
        cfg_path = f"/www/sites/{alias}/proxy/proxy.conf"
        tried_paths.append(cfg_path)

    # Path 2: Alternative conf.d path
    cfg_path2 = f"/www/conf.d/{alias}.conf"
    tried_paths.append(cfg_path2)

    # Path 3: /opt/1panel path
    cfg_path3 = f"/opt/1panel/www/conf.d/{alias}.conf"
    tried_paths.append(cfg_path3)

    for cfg_path in tried_paths:
        try:
            resp = pc.read_file(cfg_path)
            if resp.get("code") != 200:
                continue
            content = resp.get("data", {}).get("content", "")
            if not content:
                content = resp.get("data", "")
            if isinstance(content, str) and "client_max_body_size" not in content:
                # Insert after server_name line (before the first location block)
                lines = content.split("\n")
                new_lines = []
                inserted = False
                for line in lines:
                    new_lines.append(line)
                    if not inserted and "server_name" in line:
                        new_lines.append("    client_max_body_size 128m;")
                        inserted = True
                if inserted:
                    new_content = "\n".join(new_lines)
                    pc.save_file(cfg_path, new_content)
                    pc.reload_openresty()
                    logger.info("nginx: added client_max_body_size to %s", cfg_path)
                    return True
        except Exception as e:
            logger.info("nginx: tried %s — %s", cfg_path, e)
            continue

    logger.info("nginx: could not add client_max_body_size for alias=%s (paths: %s)", alias, tried_paths)
    return False


def _get_cf_credentials(account_id=None):
    """Get Cloudflare API token. Supports multi-account via account_id."""
    if account_id:
        acct = get_cf_account(account_id)
        if acct:
            return {"api_token": acct["api_token"] or None}

    acct = get_default_cf_account()
    if acct and acct.get("api_token"):
        return {"api_token": acct["api_token"] or None}

    conn = get_db()
    try:
        row = conn.execute("SELECT config_value FROM global_config WHERE config_key = 'cf_api_token'").fetchone()
        token = row["config_value"].strip() if row and row["config_value"] and row["config_value"].strip() else None
        return {"api_token": token}
    finally:
        conn.close()


def _get_cf_token(account_id=None):
    """Get Cloudflare API token string."""
    return _get_cf_credentials(account_id).get("api_token")


def _get_config_value(key: str) -> str:
    """Read a single value from global_config, or return empty string."""
    cfg = get_global_config()
    return cfg.get(key, "")


def _set_config_value(key: str, value: str) -> None:
    """Write a single key/value to global_config."""
    update_global_config(key, value)


def _now_ts() -> str:
    """Return current UTC timestamp for download filenames."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# Check Docker socket on startup (module load)
_check_docker_socket()


def register_routes(app):
    register_bridge_routes(app)
    # AI config status tracking (key=config_key, value={status,steps,message})
    ai_config_status = {}
    # Brand kit generation status tracking (key=kit_id, value={status,message,steps})
    brand_kit_generation_status = {}

    # ---- Auth helpers ----
    def admin_required(fn):
        """Decorator: JWT + admin role required."""
        @functools.wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            claims = get_jwt()
            if claims.get("role") != "admin":
                return jsonify({"code": 403, "message": "需要管理员权限", "data": None}), 403
            return fn(*args, **kwargs)
        return wrapper

    def get_current_user_id():
        """Get current user ID from JWT claims."""
        claims = get_jwt()
        return claims.get("user_id")

    # Internal API token for serving plugin ZIPs to WP containers
    import secrets as _secrets
    app.config.setdefault('INTERNAL_API_TOKEN', _secrets.token_urlsafe(32))

    # ---- Internal plugin serving (no JWT — used by WP containers) ----

    @app.route("/api/internal/plugins/<slug>", methods=["GET"])
    def internal_serve_plugin(slug):
        token = request.args.get("token", "")
        expected = current_app.config.get("INTERNAL_API_TOKEN", "")
        if not token or not expected or token != expected:
            return jsonify({"code": 403, "message": "Forbidden"}), 403
        import re as _re
        if not _re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
            return jsonify({"code": 400, "message": "Invalid slug"}), 400
        zip_path = os.path.join(PLUGINS_DIR, f"{slug}.zip")
        if not os.path.isfile(zip_path):
            return jsonify({"code": 404, "message": "Plugin not cached"}), 404
        return send_file(zip_path, mimetype="application/zip",
                         as_attachment=True, download_name=f"{slug}.zip")

    # ---- Public Feed serving (no auth — fetched by GMC) ----

    @app.route("/api/public/feed/<int:site_id>.xml", methods=["GET"])
    def serve_feed_xml(site_id):
        """Serve generated feed XML file for Google Merchant Center."""
        data_dir = os.environ.get("WP_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
        feed_file = os.path.join(data_dir, "feeds", f"{site_id}.xml")
        if not os.path.isfile(feed_file):
            return jsonify({"code": 404, "message": "Feed not found"}), 404
        return send_file(feed_file, mimetype="application/rss+xml")

    # ---- Auth ----

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True) or {}
        username = data.get("username", "")
        password = data.get("password", "")

        # Try DB-backed user first
        user = get_user_by_username(username)
        if user and check_password_hash(user['password'], password):
                additional_claims = {"user_id": user['id'], "role": user['role']}
                token = create_access_token(identity=username, additional_claims=additional_claims)
                panel_env = get_user_panel_environment(user['id'])
                return jsonify({"code": 200, "message": "Login successful",
                               "data": {"token": token, "username": username, "role": user['role'],
                                        "user_id": user['id'],
                                        "panel_environment_id": user.get("panel_environment_id"),
                                        "panel_environment": {"name": panel_env["name"], "host": panel_env["host"]} if panel_env else None}})

        # Fallback: legacy hardcoded admin credentials
        if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
            token = create_access_token(identity=username, additional_claims={"user_id": 1, "role": "admin"})
            return jsonify({"code": 200, "message": "Login successful",
                           "data": {"token": token, "username": username, "role": "admin", "user_id": 1}})

        return jsonify({"code": 401, "message": "Invalid credentials", "data": None}), 401

    @app.route("/api/auth/check", methods=["GET"])
    @jwt_required()
    def auth_check():
        claims = get_jwt()
        user_id = claims.get("user_id")
        panel_env = get_user_panel_environment(user_id) if user_id else None
        user = get_user_by_id(user_id) if user_id else {}
        return jsonify({"code": 200, "message": "OK",
                       "data": {"username": get_jwt_identity(),
                                "role": claims.get("role"),
                                "user_id": user_id,
                                "panel_environment_id": user.get("panel_environment_id") if user else None,
                                "panel_environment": {"name": panel_env["name"], "host": panel_env["host"]} if panel_env else None}})

    # ---- User self-service: set own panel environment ----
    @app.route("/api/user/panel-environment", methods=["PUT"])
    @jwt_required()
    def user_set_panel_environment():
        """Allow the current user to select their own 1Panel environment."""
        claims = get_jwt()
        user_id = claims.get("user_id")
        if not user_id:
            return jsonify({"code": 400, "message": "无效用户"}), 400
        data = request.get_json(silent=True) or {}
        env_id = data.get("panel_environment_id")
        if env_id is None:
            return jsonify({"code": 400, "message": "缺少 panel_environment_id"}), 400
        # Validate the environment exists
        env = get_panel_environment(env_id)
        if not env:
            return jsonify({"code": 404, "message": "环境不存在"}), 404
        update_user(user_id, panel_environment_id=env_id)
        return jsonify({"code": 200, "message": "环境已设置",
                        "data": {"panel_environment": {"name": env["name"], "host": env["host"]}}})

    # ---- User Management (admin only) ----

    @app.route("/api/users", methods=["GET"])
    @admin_required
    def list_users():
        try:
            users = get_all_users()
            return jsonify({"code": 200, "data": users})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/users", methods=["POST"])
    @admin_required
    def create_user_route():
        try:
            data = request.get_json(silent=True) or {}
            username = (data.get("username") or "").strip()
            password = (data.get("password") or "").strip()
            if not username or not password:
                return jsonify({"code": 400, "message": "用户名和密码不能为空"}), 400
            if get_user_by_username(username):
                return jsonify({"code": 400, "message": "用户名已存在"}), 400
            user = create_user(username, password, data.get("role", "operator"),
                              panel_environment_id=data.get("panel_environment_id"))
            return jsonify({"code": 200, "data": user, "message": "用户已创建"}), 201
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/users/<int:user_id>", methods=["PUT"])
    @admin_required
    def update_user_route(user_id):
        try:
            user = get_user_by_id(user_id)
            if not user:
                return jsonify({"code": 404, "message": "用户不存在"}), 404
            data = request.get_json(silent=True) or {}
            updated = update_user(
                user_id,
                username=(data.get("username") or "").strip() or None,
                password=(data.get("password") or "").strip() or None,
                role=data.get("role"),
                panel_environment_id=data.get("panel_environment_id"),
            )
            return jsonify({"code": 200, "data": updated, "message": "已更新"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/users/<int:user_id>", methods=["DELETE"])
    @admin_required
    def delete_user_route(user_id):
        try:
            user = get_user_by_id(user_id)
            if not user:
                return jsonify({"code": 404, "message": "用户不存在"}), 404
            if user["role"] == "admin":
                # Check if this is the last admin
                all_users = get_all_users()
                admin_count = sum(1 for u in all_users if u["role"] == "admin")
                if admin_count <= 1:
                    return jsonify({"code": 400, "message": "不能删除最后一个管理员"}), 400
            delete_user(user_id)
            return jsonify({"code": 200, "message": "已删除"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Fingerprint Category Management (admin only) ----

    @app.route("/api/fingerprint-categories", methods=["GET"])
    @jwt_required()
    def list_fingerprint_categories():
        try:
            cats = get_all_fingerprint_categories()
            return jsonify({"code": 200, "data": cats})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/fingerprint-categories", methods=["POST"])
    @admin_required
    def create_fingerprint_category_route():
        try:
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"code": 400, "message": "分类名称不能为空"}), 400
            user_id = data.get("user_id")
            cat = create_fingerprint_category(name, user_id=user_id)
            return jsonify({"code": 200, "data": cat, "message": "分类已创建"}), 201
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/fingerprint-categories/<int:cat_id>", methods=["DELETE"])
    @admin_required
    def delete_fingerprint_category_route(cat_id):
        try:
            delete_fingerprint_category(cat_id)
            return jsonify({"code": 200, "message": "分类已删除"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Profile Category Mapping (CloakBrowser profiles ← categories) ----

    @app.route("/api/profile-categories", methods=["GET"])
    @jwt_required()
    def list_profile_categories_route():
        """List all profile-category mappings."""
        try:
            data = list_profile_categories()
            return jsonify({"code": 200, "data": data})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/cloakbrowser/profiles/<name>/category", methods=["PUT"])
    @admin_required
    def set_profile_category_route(name):
        """Assign a category to a CloakBrowser profile."""
        try:
            data = request.get_json(silent=True) or {}
            category_id = data.get("category_id")
            set_profile_category(name, category_id)
            return jsonify({"code": 200, "message": "分类已更新"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/cloakbrowser/profiles/<name>/category", methods=["GET"])
    @jwt_required()
    def get_profile_category_route(name):
        """Get category assigned to a profile."""
        try:
            cat = get_profile_category(name)
            return jsonify({"code": 200, "data": cat})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Sites ----

    @app.route("/api/sites", methods=["GET"])
    @jwt_required()
    def get_sites():
        try:
            claims = get_jwt()
            user_id = None if claims.get("role") == "admin" else claims.get("user_id")
            sites = list_sites(user_id=user_id)
            # Enrich with 1Panel live data
            try:
                panel_resp = _get_panel_client().search_websites()
                if panel_resp.get("code") == 200:
                    panel_items = (panel_resp.get("data") or {}).get("items") or []
                    panel_sites = {s["id"]: s for s in panel_items}
                    for site in sites:
                        pwid = site.get("panel_website_id")
                        if pwid and pwid in panel_sites:
                            ps = panel_sites[pwid]
                            site["panel_status"] = ps.get("status", "")
                            site["panel_protocol"] = ps.get("protocol", "")
                            site["panel_alias"] = ps.get("alias", "")
                        elif pwid:
                            # Panel website no longer exists
                            site["panel_status"] = "deleted"
                        else:
                            site["panel_status"] = site.get("panel_status") or "unlinked"
            except Exception as e:
                logger.warning(f"Failed to enrich sites from panel: {e}")
            return jsonify({"code": 200, "data": sites})
        except Exception as e:
            logger.error(f"Failed to list sites: {e}")
            return jsonify({"code": 500, "message": f"获取站点列表失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites", methods=["POST"])
    @jwt_required()
    def add_site():
        try:
            data = request.get_json(silent=True) or {}
            data["created_by"] = get_current_user_id()
            site = create_site(data)
            return jsonify({"code": 200, "data": site}), 201
        except Exception as e:
            logger.error(f"Failed to create site: {e}")
            return jsonify({"code": 500, "message": f"创建站点失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/next-port", methods=["GET"])
    @jwt_required()
    def get_next_port():
        """Return the next available port (max existing port + 1, min 8081)."""
        conn = get_db()
        row = conn.execute("SELECT MAX(port) as max_port FROM sites").fetchone()
        conn.close()
        max_port = (row["max_port"] or 0) if row else 0
        next_port = max(max_port + 1, 8081)
        return jsonify({"code": 200, "data": {"next_port": next_port}})

    @app.route("/api/sites/<int:site_id>", methods=["GET"])
    @jwt_required()
    def get_site_detail(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "Site not found"}), 404
            # Enrich with 1Panel data
            try:
                pwid = site.get("panel_website_id")
                if pwid:
                    ws_resp = _get_panel_client().search_websites(name=site.get("site_name", ""))
                    if ws_resp.get("code") == 200:
                        for w in (ws_resp.get("data") or {}).get("items") or []:
                            if w.get("id") == pwid:
                                site["panel_status"] = w.get("status")
                                site["panel_protocol"] = w.get("protocol")
                                site["panel_alias"] = w.get("alias")
                                site["panel_domains"] = w.get("domains", [])
                                site["panel_site_path"] = w.get("sitePath", "")
                                break
                paid = site.get("panel_app_install_id")
                if paid:
                    app_resp = _get_panel_client().search_installed_apps(name="")
                    if app_resp.get("code") == 200:
                        for a in (app_resp.get("data") or {}).get("items") or []:
                            if a.get("id") == paid:
                                site["panel_app_status"] = a.get("status")
                                site["panel_app_version"] = a.get("version")
                                site["panel_app_name"] = a.get("name")
                                site["panel_container"] = a.get("container")
                                site["panel_http_port"] = a.get("httpPort")
                                break
            except Exception as e:
                logger.warning(f"Failed to enrich site detail from panel: {e}")
            return jsonify({"code": 200, "data": site})
        except Exception as e:
            logger.error(f"Failed to get site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"获取站点详情失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>", methods=["PUT"])
    @jwt_required()
    def edit_site(site_id):
        try:
            data = request.get_json(silent=True) or {}
            site = update_site(site_id, data)
            if not site:
                return jsonify({"code": 404, "message": "Site not found"}), 404
            return jsonify({"code": 200, "data": site})
        except Exception as e:
            logger.error(f"Failed to update site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"更新站点失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>", methods=["DELETE"])
    @jwt_required()
    def remove_site(site_id):
        """Delete a site and all associated server resources.

        1. WordPress: delete app install (Docker container).
        2. Delete 1Panel website — 1Panel's /websites/del with ForceDelete=true
           automatically removes the website entry AND the site directory.
        3. WordPress: delete manual nginx proxy config (not managed by 1Panel).
        4. Static: delete local temp files.
        5. Delete DB record.
        """
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            domain = site.get("url", "")
            alias = site.get("nginx_alias", "")
            cleanup_errors = []

            # ---- Step 1: Delete app install (WordPress Docker container) ----
            if site.get("panel_app_install_id"):
                try:
                    _get_panel_client().operate_installed(
                        site["panel_app_install_id"], "delete",
                        force_delete=True, delete_backup=True, delete_db=False,
                    )
                    logger.info(f"Deleted 1Panel app install {site['panel_app_install_id']}")
                except Exception as e:
                    msg = f"应用删除失败: {str(e)[:80]}"
                    logger.warning(msg)
                    cleanup_errors.append(msg)

            # ---- Step 2: Delete 1Panel website by domain ----
            # 1Panel's /websites/del with ForceDelete=true removes:
            #   - Website entry from 1Panel DB
            #   - Nginx config file
            #   - Site directory (/opt/1panel/apps/openresty/openresty/www/sites/{alias}/)
            # Use site creator's panel env
            site_pc = _get_panel_client()
            try:
                site_env = get_user_panel_environment(site.get("created_by") or 1)
                if site_env:
                    site_pc = OnePanelClient(host=site_env["host"], port=site_env["port"], api_key=site_env["api_key"])
            except Exception:
                pass

            # Try stored website_id first, then fallback to domain search
            pid = site.get("panel_website_id")
            if pid:
                logger.info(f"Using stored website_id={pid} for {domain}")
            else:
                pid = None
                if domain:
                    try:
                        pc = site_pc or _get_panel_client()
                        ws = pc.search_websites(name=domain)
                        if ws.get("code") == 200:
                            for w in (ws.get("data") or {}).get("items", []) or []:
                                if w.get("primaryDomain") == domain:
                                    pid = w.get("id")
                                    logger.info(f"Found 1Panel website id={pid} for domain={domain}")
                                    break
                    except Exception as e:
                        logger.warning(f"Search website by domain failed: {e}")
            if not pid:
                logger.warning(f"Could not find 1Panel website for domain={domain}")

            if pid:
                try:
                    pc = site_pc or _get_panel_client()
                    del_resp = pc.delete_website(
                        pid,
                        delete_app=False,
                        delete_backup=True,
                        force_delete=True,
                        delete_db=False,
                    )
                    if del_resp.get("code") == 200:
                        logger.info(f"Deleted 1Panel website {pid} (directory auto-removed by 1Panel)")
                    else:
                        msg = f"1Panel未删除(id={pid}): {del_resp.get('message', str(del_resp))[:100]}"
                        logger.warning(msg)
                        # Fallback: try to find by domain
                        if domain:
                            try:
                                ws = pc.search_websites(name=domain)
                                if ws.get("code") == 200:
                                    for w in (ws.get("data") or {}).get("items", []) or []:
                                        if w.get("primaryDomain") == domain and w.get("id") != pid:
                                            logger.info(f"Fallback: found website id={w['id']} for {domain}, deleting...")
                                            del_resp2 = pc.delete_website(w["id"], delete_app=False, delete_backup=True, force_delete=True, delete_db=False)
                                            if del_resp2.get("code") == 200:
                                                logger.info(f"Fallback: deleted 1Panel website {w['id']}")
                                            break
                            except Exception as fe:
                                logger.warning(f"Fallback search failed: {fe}")
                except Exception as e:
                    msg = f"网站删除失败: {str(e)[:80]}"
                    logger.warning(msg)
                    cleanup_errors.append(msg)

            # ---- Step 3: WordPress manual nginx proxy config cleanup ----
            # WordPress sites have an extra nginx proxy config at /opt/1panel/www/
            # that is NOT managed by 1Panel's website API. Only applies to WP sites.
            if site.get("site_type") != "static" and alias:
                try:
                    result = _get_panel_client().delete_nginx_proxy_config(alias, domain)
                    if result.get("code") == 200:
                        logger.info(f"Cleaned up nginx proxy config for {alias}")
                except Exception as e:
                    logger.warning(f"nginx配置清理失败: {e}")

            # ---- Step 4: Static site local temp cleanup ----
            if site.get("site_type") == "static":
                local_dir = f"/app/backend/static-sites/{domain}"
                try:
                    import shutil
                    if os.path.isdir(local_dir):
                        shutil.rmtree(local_dir)
                        logger.info(f"Deleted local static dir: {local_dir}")
                except Exception as e:
                    logger.warning(f"Failed to delete local static dir: {e}")

            # Cloudflare DNS: skip deletion — keep DNS for reuse

            # ---- Step 5: Delete DB record ----
            delete_site(site_id)

            if cleanup_errors:
                return jsonify({
                    "code": 207,
                    "message": f"站点已删除，但部分服务器资源清理失败: {'; '.join(cleanup_errors)}",
                })
            return jsonify({"code": 200, "message": "站点已删除（DNS 记录已保留）"})
        except Exception as e:
            logger.error(f"Failed to delete site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"删除站点失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/mirror", methods=["POST"])
    @jwt_required()
    def sites_mirror():
        """Set up Cloudflare Worker mirror proxy for selected static sites."""
        data = request.get_json(silent=True) or {}
        target = (data.get("target_url") or "").strip()
        site_ids = data.get("site_ids") or []
        if not target or not site_ids:
            return jsonify({"code": 400, "message": "请提供目标URL和站点列表"}), 400
        # Remove https:// prefix for target host
        import re
        tm = re.match(r'https?://([^/]+)', target)
        target_host = tm.group(1) if tm else target
        if not target_host:
            return jsonify({"code": 400, "message": "目标URL格式无效"}), 400

        user_id = get_current_user_id()
        results = []
        for sid in site_ids:
            site = get_site(sid)
            if not site:
                results.append({"site_id": sid, "ok": False, "error": "站点不存在"})
                continue
            if str(site.get("created_by")) != str(user_id):
                results.append({"site_id": sid, "ok": False, "error": "无权操作此站点"})
                continue
            domain = site.get("url", "")
            zone_id = site.get("cf_zone_id", "")
            if not zone_id:
                results.append({"site_id": sid, "ok": False, "error": "无Cloudflare Zone"})
                continue
            try:
                from models import get_cf_account
                # Use operator's own CF account (via panel environment binding)
                env = get_user_panel_environment(user_id)
                cf_account_id = env.get("cf_account_id") if env else None
                cf_account = get_cf_account(cf_account_id) if cf_account_id else None
                if not cf_account:
                    cf_account = get_default_cf_account()
                if not cf_account:
                    results.append({"site_id": sid, "ok": False, "error": "无CF账号，请检查1Panel环境的CF绑定"})
                    continue
                from cloudflare_client import CloudflareClient
                cf_client = CloudflareClient(api_token=cf_account["api_token"])
                # Get real CF account ID (not our internal DB ID)
                real_cf_id = cf_account.get("cf_external_id") or cf_account["id"]
                try:
                    accts_resp = cf_client._request("GET", "/accounts")
                    if accts_resp.get("success"):
                        for a in (accts_resp.get("result") or []):
                            real_cf_id = a["id"]
                            break
                except Exception:
                    pass
                alias = site.get("nginx_alias", domain)
                worker_name = f"mirror-{alias.replace('.', '-')}"
                script = (
                    "addEventListener('fetch', event => {"
                    "event.respondWith(handleRequest(event.request))"
                    "});"
                    "async function handleRequest(request) {"
                    f"const url = new URL(request.url);"
                    f"url.hostname = '{target_host}';"
                    f"let hdrs = new Headers(request.headers);"
                    f"hdrs.set('X-Forwarded-Host', '{domain}');"
                    f"hdrs.set('X-Forwarded-Proto', 'https');"
                    f"const resp = await fetch(url.toString(), {{method: request.method, headers: hdrs, body: request.body, redirect: 'follow'}});"
                    f"return new HTMLRewriter().on('a[href]', {{element(el){{"
                    f"var h=el.getAttribute('href');if(h){{h=h.replace('{target_host}','{domain}');el.setAttribute('href',h);}}"
                    f"}}}}).on('img[src]',{{element(el){{"
                    f"var s=el.getAttribute('src');if(s){{s=s.replace('{target_host}','{domain}');el.setAttribute('src',s);}}"
                    f"}}}}).on('form[action]',{{element(el){{"
                    f"var a=el.getAttribute('action');if(a){{a=a.replace('{target_host}','{domain}');el.setAttribute('action',a);}}"
                    f"}}}}).transform(resp);"
                    "}"
                )
                # Upload worker script
                up_resp = cf_client.upload_worker(real_cf_id, worker_name, script)
                if isinstance(up_resp, dict) and not up_resp.get("success"):
                    errs = up_resp.get("errors", [])
                    msg = "; ".join(e.get("message", str(e)) for e in errs)
                    raise Exception(f"Worker上传失败: {msg}")
                # Create route
                rt_resp = cf_client.create_worker_route(zone_id, f"*{domain}/*", worker_name)
                if isinstance(rt_resp, dict) and not rt_resp.get("success"):
                    errs = rt_resp.get("errors", [])
                    msg = "; ".join(e.get("message", str(e)) for e in errs)
                    raise Exception(f"路由创建失败: {msg}")
                update_site_fields(sid, {"mirror_target": target})
                results.append({"site_id": sid, "ok": True, "domain": domain})
                logger.info(f"Mirror: {domain} -> {target_host} (worker={worker_name})")
            except Exception as e:
                logger.error(f"Mirror failed for site {sid}: {e}")
                results.append({"site_id": sid, "ok": False, "error": str(e)[:100]})
        ok = sum(1 for r in results if r.get("ok"))
        return jsonify({"code": 200, "data": {"ok": ok, "results": results}, "message": f"已为 {ok} 个站点启用镜像"})

    @app.route("/api/sites/<int:site_id>/unmirror", methods=["POST"])
    @jwt_required()
    def sites_unmirror(site_id):
        """Remove Cloudflare Worker mirror proxy."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        user_id = get_current_user_id()
        if str(site.get("created_by")) != str(user_id):
            return jsonify({"code": 403, "message": "无权操作此站点"}), 403
        domain = site.get("url", "")
        zone_id = site.get("cf_zone_id", "")
        try:
            if zone_id:
                from models import get_cf_account
                env = get_user_panel_environment(user_id)
                cf_account_id = env.get("cf_account_id") if env else None
                cf_account = get_cf_account(cf_account_id) if cf_account_id else get_default_cf_account()
                if cf_account:
                    from cloudflare_client import CloudflareClient
                    cf_client = CloudflareClient(api_token=cf_account["api_token"])
                    alias = site.get("nginx_alias", domain)
                    worker_name = f"mirror-{alias.replace('.', '-')}"
                    # Delete worker route
                    routes = cf_client.list_worker_routes(zone_id)
                    if routes.get("success") or routes.get("result"):
                        for r in (routes.get("result") or []):
                            if r.get("script") == worker_name and domain in (r.get("pattern") or ""):
                                cf_client.delete_worker_route(zone_id, r["id"])
                    # Delete worker script
                    # Get real CF account ID
                    real_cf_id = cf_account["id"]
                    try:
                        accts_resp = cf_client._request("GET", "/accounts")
                        if accts_resp.get("success"):
                            for a in (accts_resp.get("result") or []):
                                real_cf_id = a["id"]; break
                    except Exception: pass
                    cf_client.delete_worker(real_cf_id, worker_name)
            update_site_fields(site_id, {"mirror_target": ""})
            return jsonify({"code": 200, "message": "镜像已取消"})
        except Exception as e:
            logger.error(f"Unmirror failed for site {site_id}: {e}")
            return jsonify({"code": 500, "message": f"取消失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/fix-website", methods=["POST"])
    @jwt_required()
    def fix_site_website(site_id):
        """Fix a site that has a WP app but no 1Panel website (OpenResty deployment).
        
        This creates the missing 1Panel deployment website and links it to the
        existing WP application, then updates the local DB.
        """
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            # Already has a website
            if site.get("panel_website_id"):
                return jsonify({"code": 400, "message": "站点已有1Panel网站，无需修复"})

            app_install_id = site.get("panel_app_install_id")
            if not app_install_id:
                return jsonify({"code": 400, "message": "站点缺少1Panel应用ID，无法修复"})

            alias = site.get("nginx_alias") or site["site_name"].replace(".", "-")
            domain = site["site_name"]
            port = site.get("port", 8081)

            # First, check if a website with this alias/domain already exists in 1Panel
            existing_website_id = None
            try:
                ws = _get_panel_client().search_websites(name=domain)
                if ws.get("code") == 200:
                    items = ws.get("data", {}).get("items") or []
                    for w in items:
                        if w.get("alias") == alias or w.get("primaryDomain") == domain:
                            existing_website_id = w.get("id")
                            logger.info(f"Fix-website: Found existing website id={existing_website_id} for {domain}")
                            break
            except Exception as e:
                logger.warning(f"Fix-website: search_websites failed: {e}")

            if existing_website_id:
                # Website already exists in 1Panel, just update our DB
                update_site_fields(site_id, {"panel_website_id": existing_website_id})
                return jsonify({
                    "code": 200,
                    "message": "1Panel网站已存在，已自动关联",
                    "data": {"panel_website_id": existing_website_id, "alias": alias},
                })

            # Ensure website group exists
            group_id = _get_panel_client().ensure_website_group()

            # Create the deployment website
            logger.info(f"Fix-website: Creating deployment website for {domain} (app_install_id={app_install_id})")
            result = _get_panel_client().create_website(
                primary_domain=domain,
                alias=alias,
                app_type="installed",
                app_install_id=app_install_id,
                website_group_id=group_id,
                enable_ipv6=False,
                port=port or 9000,
            )
            logger.info(f"Fix-website: create_website response: code={result.get('code')}, message={result.get('message','')[:200]}")

            # If alias conflict, try with a unique alias
            if result.get("code") != 200 and "标识已存在" in str(result.get("message", "")):
                for suffix in range(2, 5):
                    unique_alias = f"{alias}-{suffix}"
                    logger.info(f"Fix-website: Retrying with alias={unique_alias}")
                    result = _get_panel_client().create_website(
                        primary_domain=domain,
                        alias=unique_alias,
                        app_type="installed",
                        app_install_id=app_install_id,
                        website_group_id=group_id,
                        enable_ipv6=False,
                        port=port or 9000,
                    )
                    if result.get("code") == 200:
                        alias = unique_alias
                        break

            if result.get("code") != 200:
                return jsonify({"code": 500, "message": f"创建1Panel网站失败: {result.get('message', '未知错误')[:100]}"})

            # Find the website ID
            website_id = None
            ws = _get_panel_client().search_websites(name=domain)
            if ws.get("code") == 200:
                items = ws.get("data", {}).get("items") or []
                for w in items:
                    if w.get("alias") == alias or w.get("primaryDomain") == domain:
                        website_id = w.get("id")
                        break

            # Update local DB
            update_site_fields(site_id, {"panel_website_id": website_id, "nginx_alias": alias})

            return jsonify({
                "code": 200,
                "message": "1Panel网站已创建并关联",
                "data": {"panel_website_id": website_id, "alias": alias},
            })
        except Exception as e:
            logger.error(f"Fix-website failed for {site_id}: {e}")
            return jsonify({"code": 500, "message": f"修复失败: {str(e)[:100]}"}), 500

    # ---- CSV Export ----

    @app.route("/api/sites/export/csv", methods=["GET"])
    @jwt_required()
    def export_csv():
        try:
            import csv
            import io

            sites = list_sites()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "Site Name", "Url", "Admin Name", "Admin Password", "Tag",
                "Security ID", "HTTP Username", "HTTP Password",
                "Verify Certificate", "SSL Version",
            ])
            for s in sites:
                writer.writerow([
                    s.get("site_name", ""),
                    s.get("url", ""),
                    s.get("admin_name", ""),
                    s.get("admin_password", ""),
                    s.get("tag", ""),
                    s.get("security_id", ""),
                    s.get("http_username", ""),
                    s.get("http_password", ""),
                    "1" if s.get("verify_certificate") else "0",
                    s.get("ssl_version", "auto"),
                ])
            output.seek(0)
            return app.response_class(
                output.getvalue(),
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=wordpress_sites.csv"},
            )
        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            return jsonify({"code": 500, "message": f"导出CSV失败: {str(e)[:100]}"}), 500

    # ---- 1Panel Proxy APIs ----

    # ---- Panel Environment CRUD (admin only) ----

    @app.route("/api/panel/environments", methods=["GET"])
    @jwt_required()
    def panel_list_environments():
        try:
            envs = get_all_panel_environments()
            return jsonify({"code": 200, "data": envs})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/panel/environments", methods=["POST"])
    @jwt_required()
    def panel_create_environment():
        try:
            claims = get_jwt()
            if claims.get("role") != "admin":
                return jsonify({"code": 403, "message": "仅管理员可操作"}), 403
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"code": 400, "message": "环境名称不能为空"}), 400
            host = (data.get("host") or "").strip()
            if not host:
                return jsonify({"code": 400, "message": "主机地址不能为空"}), 400
            port = int(data.get("port", 3500))
            api_key = (data.get("api_key") or "").strip()
            if not api_key:
                return jsonify({"code": 400, "message": "API Key不能为空"}), 400
            env = create_panel_environment({
                "name": name,
                "host": host,
                "port": port,
                "api_key": api_key,
                "is_default": 0,
                "cf_account_id": data.get("cf_account_id"),
            })
            return jsonify({"code": 200, "data": env, "message": "环境已创建"}), 201
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/panel/environments/<int:env_id>", methods=["PUT"])
    @jwt_required()
    def panel_update_environment(env_id):
        try:
            claims = get_jwt()
            if claims.get("role") != "admin":
                return jsonify({"code": 403, "message": "仅管理员可操作"}), 403
            env = get_panel_environment(env_id)
            if not env:
                return jsonify({"code": 404, "message": "环境不存在"}), 404
            data = request.get_json(silent=True) or {}
            update_data = {}
            for key in ("name", "host", "port", "api_key", "cf_account_id"):
                if key in data:
                    update_data[key] = data[key]
            updated = update_panel_environment(env_id, update_data)
            return jsonify({"code": 200, "data": updated, "message": "已更新"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/panel/environments/<int:env_id>", methods=["DELETE"])
    @jwt_required()
    def panel_delete_environment(env_id):
        try:
            claims = get_jwt()
            if claims.get("role") != "admin":
                return jsonify({"code": 403, "message": "仅管理员可操作"}), 403
            env = get_panel_environment(env_id)
            if not env:
                return jsonify({"code": 404, "message": "环境不存在"}), 404

            # Check if any operators are bound to this environment
            from models import get_db
            db = get_db()
            bound_users = db.execute(
                "SELECT id, username FROM users WHERE panel_environment_id = ?", (env_id,)
            ).fetchall()
            if bound_users:
                names = ", ".join(u["username"] for u in bound_users)
                return jsonify({"code": 400, "message": f"无法删除：以下运营人员绑定了此环境：{names}。请先解除绑定。"}), 400

            # Check if any sites use this environment (via creator's binding)
            user_ids = [u["id"] for u in bound_users] if bound_users else []
            if user_ids:
                bound_sites = db.execute(
                    "SELECT COUNT(*) FROM sites WHERE created_by IN ({})".format(",".join("?"*len(user_ids))),
                    user_ids
                ).fetchone()[0]
                if bound_sites:
                    return jsonify({"code": 400, "message": f"无法删除：有 {bound_sites} 个站点关联此环境。请先删除站点或更换运营的环境。"}), 400

            delete_panel_environment(env_id)
            return jsonify({"code": 200, "message": "已删除"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/panel/environments/<int:env_id>/default", methods=["PUT"])
    @jwt_required()
    def panel_set_default_environment(env_id):
        try:
            claims = get_jwt()
            if claims.get("role") != "admin":
                return jsonify({"code": 403, "message": "仅管理员可操作"}), 403
            set_default_panel_environment(env_id)
            return jsonify({"code": 200, "message": "已设为默认"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/panel/environments/current", methods=["GET"])
    @jwt_required()
    def panel_get_current_environment():
        """Return the panel environment for the current user."""
        try:
            claims = get_jwt()
            env = get_user_panel_environment(claims.get("user_id"))
            return jsonify({"code": 200, "data": env})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500
    @jwt_required()
    def panel_search_apps():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().search_apps(
                name=data.get("name", ""),
                page=data.get("page", 1),
                page_size=data.get("pageSize", 100),
            ))
        except Exception as e:
            logger.error(f"Panel search apps failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/<key>", methods=["GET"])
    @jwt_required()
    def panel_get_app(key):
        try:
            return jsonify(_get_panel_client().get_app(key))
        except Exception as e:
            logger.error(f"Panel get app failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/detail/<int:app_id>/<version>", methods=["GET"])
    @jwt_required()
    def panel_get_app_detail(app_id, version):
        try:
            return jsonify(_get_panel_client().get_app_detail(app_id, version))
        except Exception as e:
            logger.error(f"Panel get app detail failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/install", methods=["POST"])
    @jwt_required()
    def panel_install_app():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().install_app(
                app_detail_id=data.get("appDetailId"),
                name=data.get("name"),
                params=data.get("params", {}),
                services=data.get("services"),
                advanced=data.get("advanced", False),
            ))
        except Exception as e:
            logger.error(f"Panel install app failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/installed/search", methods=["POST"])
    @jwt_required()
    def panel_search_installed():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().search_installed_apps(
                page=data.get("page", 1),
                page_size=data.get("pageSize", 100),
                name=data.get("name", ""),
                app_type=data.get("type", ""),
            ))
        except Exception as e:
            logger.error(f"Panel search installed failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/installed/list", methods=["GET"])
    @jwt_required()
    def panel_installed_list():
        try:
            return jsonify(_get_panel_client().get_installed_list())
        except Exception as e:
            logger.error(f"Panel installed list failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/installed/params/<int:install_id>", methods=["GET"])
    @jwt_required()
    def panel_installed_params(install_id):
        try:
            return jsonify(_get_panel_client().get_installed_params(install_id))
        except Exception as e:
            logger.error(f"Panel installed params failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/apps/services/<key>", methods=["GET"])
    @jwt_required()
    def panel_app_services(key):
        try:
            return jsonify(_get_panel_client().get_app_services(key))
        except Exception as e:
            logger.error(f"Panel app services failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/search", methods=["POST"])
    @jwt_required()
    def panel_search_websites():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().search_websites(
                page=data.get("page", 1),
                page_size=data.get("pageSize", 100),
                name=data.get("name", ""),
            ))
        except Exception as e:
            logger.error(f"Panel search websites failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/list", methods=["GET"])
    @jwt_required()
    def panel_websites_list():
        try:
            return jsonify(_get_panel_client().get_websites_list())
        except Exception as e:
            logger.error(f"Panel websites list failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/create", methods=["POST"])
    @jwt_required()
    def panel_create_website():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().create_website(
                primary_domain=data.get("primaryDomain", ""),
                alias=data.get("alias", ""),
                app_type=data.get("appType", "installed"),
                app_install_id=data.get("appInstallId"),
                app_detail_id=data.get("appDetailId"),
                app_id=data.get("appId"),
                app_install_params=data.get("appInstallParams"),
                services=data.get("services"),
                website_group_id=data.get("webSiteGroupId", 1),
                remark=data.get("remark", ""),
                enable_ipv6=data.get("enableIPV6", False),
                proxy=data.get("proxy", ""),
                port=data.get("port", 9000),
                runtime_type=data.get("runtimeType", "php"),
                domains=data.get("domains"),
            ))
        except Exception as e:
            logger.error(f"Panel create website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/check", methods=["POST"])
    @jwt_required()
    def panel_check_website():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().check_website(
                primary_domain=data.get("primaryDomain", ""),
                app_type=data.get("appType", "installed"),
                app_install_id=data.get("appInstallID"),
                alias=data.get("alias", ""),
            ))
        except Exception as e:
            logger.error(f"Panel check website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/<int:website_id>", methods=["GET"])
    @jwt_required()
    def panel_get_website(website_id):
        try:
            return jsonify(_get_panel_client().get_website(website_id))
        except Exception as e:
            logger.error(f"Panel get website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/<int:website_id>", methods=["DELETE"])
    @jwt_required()
    def panel_delete_website(website_id):
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().delete_website(
                website_id=website_id,
                delete_app=data.get("deleteApp", True),
                delete_backup=data.get("deleteBackup", True),
                force_delete=data.get("forceDelete", False),
                delete_db=False,
            ))
        except Exception as e:
            logger.error(f"Panel delete website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/groups/search", methods=["POST"])
    @jwt_required()
    def panel_search_groups():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().search_groups(data.get("type", "website")))
        except Exception as e:
            logger.error(f"Panel search groups failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    @app.route("/api/panel/websites/operate", methods=["POST"])
    @jwt_required()
    def panel_operate_website():
        try:
            data = request.get_json(silent=True) or {}
            return jsonify(_get_panel_client().operate_website(
                website_id=data.get("id"),
                operate=data.get("operate", ""),
            ))
        except Exception as e:
            logger.error(f"Panel operate website failed: {e}")
            return jsonify({"code": 502, "message": f"1Panel连接失败: {str(e)[:80]}"}), 502

    def _sync_feed_to_static_site(site, products):
        """Upload feed XML to 1Panel static site directory."""
        import xml.etree.ElementTree as ET
        import json as _j

        domain = site["url"]
        ns_g = "http://base.google.com/ns/1.0"
        rss = ET.Element("rss", {"version": "2.0", "xmlns:g": ns_g})
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = site.get("site_name") or domain
        ET.SubElement(channel, "link").text = f"https://{domain}"
        ET.SubElement(channel, "description").text = "Google Shopping Product Feed"

        # Pre-load static site products to map local IDs for product page URLs
        local_products = list_static_site_products(site["id"])
        local_by_sku = {lp.get("sku"): lp for lp in local_products if lp.get("sku")}
        local_by_title = {lp.get("title", "").strip().lower(): lp for lp in local_products if lp.get("title")}

        for p in products:
            # Resolve correct product page URL on the static site
            feed_sku = p.get("item_id") or p.get("sku", "")
            feed_title = (p.get("title") or "").strip().lower()
            local = local_by_sku.get(feed_sku) if feed_sku else None
            if not local:
                local = local_by_title.get(feed_title)
            if local:
                product_url = f"https://{domain}/products/{local['id']}/"
            else:
                product_url = p.get("source_url") or f"https://{domain}"

            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "g:id").text = str(p.get("id", ""))
            ET.SubElement(item, "g:title").text = (p.get("title") or "")[:150]
            ET.SubElement(item, "g:description").text = (p.get("description") or "")[:5000]
            ET.SubElement(item, "g:link").text = product_url

            images = p.get("images") or []
            if isinstance(images, str):
                try: images = _j.loads(images)
                except Exception: images = [images] if images else []
            if isinstance(images, list) and images:
                ET.SubElement(item, "g:image_link").text = str(images[0])
                for img in images[1:11]:
                    ET.SubElement(item, "g:additional_image_link").text = str(img)

            price = (p.get("price") or "").replace("$", "").replace(",", "").strip()
            if price:
                ET.SubElement(item, "g:price").text = f"{price} {p.get('currency', 'USD')}"
            ET.SubElement(item, "g:availability").text = "in_stock"
            ET.SubElement(item, "g:condition").text = "new"
            if p.get("brand"):
                ET.SubElement(item, "g:brand").text = str(p["brand"])[:70]

        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")
        size_bytes = len(xml_str.encode("utf-8"))

        # Upload via SSH
        nginx_alias = site.get("nginx_alias", "")
        site_dir = site.get("static_dir", "")
        env = get_user_panel_environment(site.get("created_by") or 1)
        if env and nginx_alias and site_dir:
            from ssh_client import get_ssh_client
            ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
            ssh.write_file(f"{site_dir}/feed.xml", xml_str)
            ssh.reload_nginx()

        feed_url = f"https://{domain}/feed.xml"
        update_site(site["id"], {"google_feed_url": feed_url})
        return jsonify({
            "code": 200,
            "data": {"feed_url": feed_url, "products": len(products), "size_bytes": size_bytes},
        })

    def _clean_feed_from_static_site(site):
        """Remove feed.xml from 1Panel static site directory."""
        import xml.etree.ElementTree as ET
        domain = site["url"]
        ns_g = "http://base.google.com/ns/1.0"
        rss = ET.Element("rss", {"version": "2.0", "xmlns:g": ns_g})
        channel = ET.SubElement(rss, "channel")
        ET.SubElement(channel, "title").text = site.get("site_name") or domain
        ET.SubElement(channel, "link").text = f"https://{domain}"
        ET.SubElement(channel, "description").text = "Google Shopping Product Feed"
        empty_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")

        nginx_alias = site.get("nginx_alias", "")
        site_dir = site.get("static_dir", "")
        env = get_user_panel_environment(site.get("created_by") or 1)
        if env and nginx_alias and site_dir:
            from ssh_client import get_ssh_client
            ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
            ssh.write_file(f"{site_dir}/feed.xml", empty_xml)
            ssh.reload_nginx()
            update_site(site["id"], {"google_feed_url": ""})
            return True
        return False

    def _regenerate_static_site_html(task_id_or_none, site_id):
        """Regenerate static site files and upload to 1Panel (used after product sync)."""
        site = get_site(site_id)
        if not site or site.get("site_type") != "static":
            return
        domain = site.get("url", "")
        products = list_static_site_products(site_id)
        brand_kit_id = site.get("brand_kit_id")
        brand_kit = get_brand_kit(brand_kit_id) if brand_kit_id else {}

        # Generate files in memory
        from static_store_engine import render_site_to_dict
        files = render_site_to_dict(domain, brand_kit or {}, products or [])
        logger.info(f"Regenerate: {domain} with {len(products)} products, {len(files)} files")

        # Upload via SSH
        static_dir = site.get("static_dir", "")
        if static_dir:
            try:
                env = get_user_panel_environment(site.get("created_by") or 1)
                from ssh_client import get_ssh_client
                ssh = get_ssh_client(env["host"], 22, env.get("ssh_password", ""))
            except Exception:
                return

            uploaded = 0
            for rel_path, content in files.items():
                if rel_path.endswith(".css") or rel_path.endswith(".js"):
                    continue
                remote_path = f"{static_dir}/{rel_path}"
                ssh.write_file(remote_path, content)
                uploaded += 1
            ssh.reload_nginx()
            logger.info(f"Regenerate uploaded {uploaded} files to server for {domain}")

# _generate_brand_pages removed — replaced by static_store_engine.render_site()

    def _bg_deploy_static(task_id, site_id, alias, domain,
                         brand_kit=None, panel_host="", panel_port=22, panel_api_key=""):
        """Deploy static site via SSH: 1. create dir  2. design  3. upload  4. activate."""
        from ssh_client import get_ssh_client
        site_dir = f"/www/sites/{alias}/index"
        total_files = 0

        try:
            # ── Step 1: Create site directory via SSH ──
            update_bg_task(task_id, status="deploying", message="正在创建站点目录...")
            ssh = get_ssh_client(panel_host, panel_port, panel_api_key)
            ssh.mkdir_p(site_dir)
            update_site_fields(site_id, {"stitch_design_status": "pending"})
            logger.info(f"Deploy static site: domain={domain} site_dir={site_dir}")

            # ── Step 2: Generate page designs ──
            update_bg_task(task_id, status="deploying", message="正在生成商城页面...")
            update_site_fields(site_id, {"stitch_design_status": "starting"})
            from static_store_engine import render_site_to_dict

            stitch_used = False
            def design_progress(msg):
                nonlocal stitch_used
                if "Stitch" in msg:
                    stitch_used = True
                    update_site_fields(site_id, {"stitch_design_status": "generating"})
                update_bg_task(task_id, status="deploying", message=msg)

            files = render_site_to_dict(domain, brand_kit or {}, [],
                progress_callback=design_progress)

            page_count = len(files)
            stitch_msg = " (Stitch)" if stitch_used else ""
            update_site_fields(site_id, {"stitch_design_status": "complete"})
            logger.info(f"Generated {page_count} files for {domain}{stitch_msg}")
            update_bg_task(task_id, status="deploying",
                          message=f"页面生成完成（{page_count} 个文件）{stitch_msg}")

            # ── Step 3: Upload files via SSH ──
            uploaded = 0
            total_files = len([r for r in files if not r.endswith(".css") and not r.endswith(".js")])

            for rel_path, content in files.items():
                if rel_path.endswith(".css") or rel_path.endswith(".js"):
                    continue
                remote_path = f"{site_dir}/{rel_path}"
                ssh.write_file(remote_path, content)
                uploaded += 1
                if uploaded % 3 == 0 or uploaded == total_files:
                    update_bg_task(task_id, status="deploying",
                                  message=f"正在上传文件... ({uploaded}/{total_files})")

            ssh.reload_nginx()
            logger.info(f"Uploaded {uploaded}/{total_files} files for {domain}")

            # ── Step 4: Activate site ──
            update_site_fields(site_id, {
                "status": "active",
                "static_dir": site_dir,
            })
            design_label = "Stitch设计" if stitch_used else "标准设计"
            update_bg_task(task_id, status="completed",
                          message=f"部署完成 — {design_label}，{uploaded} 个文件")

        except Exception as e:
            logger.error(f"Static deployment failed for {domain}: {traceback.format_exc()}")
            update_bg_task(task_id, status="failed", message=f"部署失败: {str(e)[:200]}")
            update_site_fields(site_id, {"status": "error"})

    # ---- Static Site Deployment ----

    @app.route("/api/sites/create-static", methods=["POST"])
    @jwt_required()
    def batch_create_static_site():
        """Create static e-commerce sites. No WordPress, no database, no containers.

        Flow:
        1. For each domain: create site DB record (site_type="static")
        2. Create Cloudflare DNS record
        3. Spawn background thread to deploy static HTML via 1Panel
        """
        try:
            data = request.get_json(silent=True) or {}
            domains = data.get("domains", [])
            if not domains:
                return jsonify({"code": 400, "message": "未提供域名"}), 400

            brand_kit_id = data.get("brand_kit_id")
            cf_account_id = data.get("cf_account_id")
            tag = data.get("tag", "静态独立站")
            admin_name = data.get("admin_name", "admin")
            admin_password = data.get("admin_password", "")

            # Resolve brand kit (contains all site data)
            brand_kit = None
            if brand_kit_id:
                brand_kit = get_brand_kit(int(brand_kit_id))
                if not brand_kit:
                    return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
                if brand_kit.get("status") != "ready":
                    return jsonify({"code": 400, "message": f"品牌套件未就绪 (当前状态: {brand_kit['status']})"}), 400

            # Resolve CF account and panel environment
            cf_account = None
            panel_env = None
            # Get current user
            current_user = get_jwt_identity()
            user = get_user_by_username(current_user)
            user_id = user["id"] if user else None

            # Resolve panel environment: CF account first, then user's bound env
            panel_env = None
            if cf_account_id:
                cf_account = get_cf_account(int(cf_account_id))
                if cf_account:
                    panel_env = get_environment_by_cf_account(int(cf_account_id))
            if not panel_env and user_id:
                panel_env = get_user_panel_environment(user_id)

            # CF account isolation: enforce operator's bound CF account
            if panel_env and panel_env.get("cf_account_id"):
                env_cf_id = str(panel_env["cf_account_id"])
                inbound_cf_id = str(cf_account_id) if cf_account_id else ""
                if inbound_cf_id and inbound_cf_id != env_cf_id:
                    return jsonify({"code": 403, "message": f"无权使用该Cloudflare账号。请使用您运营环境绑定的CF账号。"}), 403
                # Auto-correct: if no cf_account_id provided, use the env's bound one
                if not inbound_cf_id:
                    cf_account_id = panel_env["cf_account_id"]
                    cf_account = get_cf_account(int(cf_account_id))

            # Get global config for panel defaults
            global_cfg = get_global_config()
            panel_server_ip = global_cfg.get("panel_server_ip", "")

            # Generate security ID
            try:
                conn = get_db()
                row = conn.execute(
                    "SELECT MAX(CAST(security_id AS INTEGER)) FROM sites WHERE security_id GLOB '[0-9]*'"
                ).fetchone()
                conn.close()
                security_id = str((row[0] or 0) + 1)
            except Exception:
                security_id = "1"

            results = []

            for dom in domains:
                domain = dom.get("domain", "").strip()
                if not domain:
                    continue

                # Per-domain brand kit (batch wizard: each row has its own kit)
                dom_brand_kit_id = dom.get("brand_kit_id") or brand_kit_id
                dom_brand_kit = brand_kit
                if dom_brand_kit_id and str(dom_brand_kit_id) != str(brand_kit_id or ""):
                    try:
                        dom_brand_kit = get_brand_kit(int(dom_brand_kit_id))
                        if not dom_brand_kit or dom_brand_kit.get("status") != "ready":
                            dom_brand_kit = None
                    except Exception:
                        dom_brand_kit = None

                # 1Panel accepts dots in alias, use domain directly
                alias = domain

                # Create site record
                site_data = {
                    "site_name": domain,
                    "url": domain,
                    "admin_name": admin_name,
                    "admin_password": admin_password,
                    "tag": tag,
                    "security_id": security_id,
                    "status": "deploying",
                    "site_type": "static",
                    "static_dir": f"/www/sites/{alias}/index",
                    "brand_kit_id": dom_brand_kit_id,
                    "nginx_alias": alias,
                    "created_by": user_id,
                    "cloakbrowser_profile_name": dom_brand_kit.get("cloakbrowser_profile_name") if dom_brand_kit else None,
                }
                site = create_site(site_data)
                site_id = site["id"]

                # Cloudflare DNS
                cf_result = None
                if cf_account:
                    cf_api_token = cf_account.get("api_token", "")
                    if cf_api_token:
                        try:
                            from cloudflare_client import CloudflareClient
                            cf_client = CloudflareClient(cf_api_token)
                            # Resolve zone: try domain itself, then parent domain
                            zone = cf_client.find_zone_by_name(domain)
                            zone_name = domain
                            if not zone:
                                # Extract parent domain for subdomains
                                parts = domain.rstrip(".").split(".")
                                if len(parts) > 2:
                                    parent = ".".join(parts[-2:])  # e.g. lhwebs.com
                                    zone = cf_client.find_zone_by_name(parent)
                                    if zone:
                                        zone_name = parent
                                # If still no zone, create one for the root domain
                                if not zone:
                                    root = ".".join(parts[-2:]) if len(parts) >= 2 else domain
                                    zone = cf_client.create_zone(root)
                                    zone_name = root
                            zone_id = zone.get("id") if isinstance(zone, dict) else None
                            if zone_id:
                                # For root domain, name=domain; for subdomain, name=domain (full FQDN)
                                # DNS target = 1Panel environment IP (always use panel env)
                                target_ip = (panel_env.get("host", "") if panel_env else "") or panel_server_ip
                                if not target_ip:
                                    logger.warning(f"DNS: no target IP for {domain}, skipping")
                                else:
                                    # Check if A record already exists
                                    existing = cf_client.list_dns_records(zone_id, "A", domain)
                                    existing_rec = None
                                    if existing and existing.get("success") and existing.get("result"):
                                        for rec in existing["result"]:
                                            if rec.get("name") == domain or rec.get("name") == f"{domain}.{zone_name}":
                                                existing_rec = rec
                                                break

                                    if existing_rec:
                                        existing_ip = existing_rec.get("content", "")
                                        if existing_ip == target_ip:
                                            logger.info(f"DNS: {domain} already points to {target_ip}, keeping")
                                            cf_result = {"success": True, "result": existing_rec}
                                        else:
                                            logger.info(f"DNS: {domain} points to {existing_ip}, updating to {target_ip}")
                                            upd = cf_client.update_dns_record(zone_id, existing_rec["id"], {
                                                "type": "A", "name": domain, "content": target_ip, "proxied": True, "ttl": 1,
                                            })
                                            cf_result = upd if upd and upd.get("success") else None
                                            if cf_result:
                                                logger.info(f"DNS: {domain} updated to {target_ip}")
                                    else:
                                        logger.info(f"DNS: creating A record {domain} → {target_ip}")
                                        dns = cf_client.create_dns_record(
                                            zone_id=zone_id,
                                            record_type="A",
                                            name=domain,
                                            content=target_ip,
                                            proxied=True,
                                        )
                                        cf_result = dns if dns and dns.get("success") else None

                                    if cf_result:
                                        rec = cf_result.get("result", {})
                                        update_site_fields(site_id, {
                                            "cf_zone_id": zone_id,
                                            "cf_dns_record_id": rec.get("id") if isinstance(rec, dict) else None,
                                        })
                                        # Auto-set SSL to Flexible for Cloudflare proxied sites
                                        try:
                                            ssl_result = cf_client.set_ssl_mode(zone_id, "flexible")
                                            if ssl_result and ssl_result.get("success"):
                                                logger.info(f"SSL: {domain} set to flexible")
                                            else:
                                                logger.warning(f"SSL: set_ssl_mode failed for {domain}: {ssl_result.get('errors') if ssl_result else 'no response'}")
                                        except Exception as ssl_e:
                                            logger.warning(f"SSL: set_ssl_mode error for {domain}: {ssl_e}")
                        except Exception as e:
                            logger.warning(f"Cloudflare DNS for {domain} failed: {e}")

                # Initialize bg task
                bg_task_id = create_bg_task(site_id, "deploy_static", status="queued", message="排队等待部署...")

                panel_host = panel_env.get("host") if panel_env else ""
                panel_port = panel_env.get("port", 3500) if panel_env else 3500
                panel_key = panel_env.get("api_key", "") if panel_env else ""

                thread = threading.Thread(
                    target=_bg_deploy_static,
                    args=(bg_task_id, site_id, alias, domain),
                    kwargs={
                        "brand_kit": dom_brand_kit,
                        "panel_host": panel_host,
                        "panel_port": panel_port,
                        "panel_api_key": panel_key,
                    },
                    daemon=True,
                )
                thread.start()

                results.append({
                    "site_id": site_id,
                    "domain": domain,
                    "task_id": str(bg_task_id),
                    "cf_result": cf_result,
                    "status": "deploying",
                })

            return jsonify({
                "code": 200,
                "message": f"已创建 {len(results)} 个静态站点部署任务",
                "data": results,
            })

        except Exception as e:
            logger.error(f"Static site creation failed: {traceback.format_exc()}")
            return jsonify({"code": 500, "message": f"创建失败: {str(e)[:200]}"}), 500

    # ---- Static Site Feed Generation ----

    def _generate_static_feed(site_id, site):
        """Generate Google Shopping Feed XML from static_site_products table.
        Also uploads the feed.xml to 1Panel site directory.
        """
        try:
            products = list_static_site_products(site_id)
            if not products:
                return jsonify({"code": 400, "message": "站点没有产品，请先导入产品"}), 400

            domain = site.get("url", "")
            brand_name = ""
            brand_kit_id = site.get("brand_kit_id")
            if brand_kit_id:
                brand_kit = get_brand_kit(brand_kit_id)
                if brand_kit:
                    brand_name = brand_kit.get("brand_name", "")

            # Build RSS + Google Shopping XML
            import xml.etree.ElementTree as ET
            rss = ET.Element("rss", {
                "version": "2.0",
                "xmlns:g": "http://base.google.com/ns/1.0",
            })
            channel = ET.SubElement(rss, "channel")
            ET.SubElement(channel, "title").text = domain
            ET.SubElement(channel, "link").text = f"https://{domain}"
            ET.SubElement(channel, "description").text = "Google Shopping Product Feed"

            for p in products:
                item = ET.SubElement(channel, "item")
                pid = str(p.get("id", ""))
                ET.SubElement(item, "g:id").text = pid
                ET.SubElement(item, "g:title").text = (p.get("title") or "")[:150]
                desc = (p.get("description") or "")
                # Strip HTML from description
                import re as _re
                desc = _re.sub(r"<[^>]+>", "", desc)[:5000]
                ET.SubElement(item, "g:description").text = desc
                product_url = p.get("product_url", "") or f"https://{domain}/products/{pid}"
                ET.SubElement(item, "g:link").text = product_url
                image = p.get("image_url", "")
                if image:
                    ET.SubElement(item, "g:image_link").text = image
                # Additional images
                add_images = p.get("additional_images", [])
                if isinstance(add_images, str):
                    try:
                        add_images = json.loads(add_images)
                    except Exception:
                        add_images = []
                for img in add_images[:10]:
                    if img:
                        ET.SubElement(item, "g:additional_image_link").text = img

                price = p.get("price", 0) or 0
                currency = p.get("currency", "USD")
                ET.SubElement(item, "g:price").text = f"{float(price):.2f} {currency}"
                sale_price = p.get("sale_price")
                if sale_price and float(sale_price) > 0:
                    ET.SubElement(item, "g:sale_price").text = f"{float(sale_price):.2f} {currency}"

                ET.SubElement(item, "g:availability").text = p.get("availability", "in_stock")
                ET.SubElement(item, "g:condition").text = p.get("condition", "new")
                b = p.get("brand", "") or brand_name
                if b:
                    ET.SubElement(item, "g:brand").text = b
                mpn = p.get("mpn", "") or p.get("sku", "")
                if mpn:
                    ET.SubElement(item, "g:mpn").text = mpn
                gtin = p.get("gtin", "")
                if gtin:
                    ET.SubElement(item, "g:gtin").text = gtin
                category = p.get("category", "")
                if category:
                    ET.SubElement(item, "g:product_type").text = category
                weight = p.get("shipping_weight", "")
                if weight:
                    unit = p.get("shipping_weight_unit", "kg")
                    ET.SubElement(item, "g:shipping_weight").text = f"{weight} {unit}"

            xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n'
            xml_str += ET.tostring(rss, encoding="unicode")

            # Save locally
            feed_dir = os.path.join(os.path.dirname(__file__), "data", "feeds")
            os.makedirs(feed_dir, exist_ok=True)
            local_path = os.path.join(feed_dir, f"{site_id}.xml")
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(xml_str)

            # Upload to 1Panel site directory for public access
            nginx_alias = site.get("nginx_alias", "")
            feed_url = f"https://{domain}/feed.xml"
            if nginx_alias:
                try:
                    from panel_client import panel_client as _pc
                    env = get_user_panel_environment(site.get("created_by") or 1)
                    if env:
                        pc = OnePanelClient(
                            host=env.get("host", ""),
                            port=env.get("port", 3500),
                            api_key=env.get("api_key", ""),
                        )
                        pc.upload_static_site_files(
                            alias=nginx_alias,
                            files={"feed.xml": xml_str},
                        )
                except Exception as ue:
                    logger.warning(f"Upload feed.xml to 1Panel failed: {ue}")

            update_site(site_id, {"google_feed_url": feed_url})

            return jsonify({
                "code": 200,
                "message": "Feed 生成成功",
                "data": {
                    "feed_url": feed_url,
                    "product_count": len(products),
                    "local_path": local_path,
                },
            })
        except Exception as e:
            logger.error(f"Static feed generation error site={site_id}: {traceback.format_exc()}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Static Site Product Management ----

    @app.route("/api/sites/<int:site_id>/static-products", methods=["GET"])
    @jwt_required()
    def list_site_products(site_id):
        """List all products for a static site."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        products = list_static_site_products(site_id)
        return jsonify({"code": 200, "data": products})

    @app.route("/api/sites/<int:site_id>/static-products", methods=["POST"])
    @jwt_required()
    def create_site_product(site_id):
        """Add a single product to a static site."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        data = request.get_json(silent=True) or {}
        data["site_id"] = site_id
        product = create_static_site_product(data)
        return jsonify({"code": 200, "data": product})

    @app.route("/api/sites/<int:site_id>/import-csv", methods=["POST"])
    @jwt_required()
    def import_csv_products(site_id):
        """Parse WooCommerce CSV and import products to static site."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        # Support JSON body import (from preview selection)
        json_data = request.get_json(silent=True) or {}
        if json_data.get("action") == "import_list":
            products = json_data.get("products", [])
            if not products:
                return jsonify({"code": 400, "message": "产品列表为空"}), 400
            count = 0
            for p in products:
                # Map CSV fields to woocommerce_products schema
                images = p.get("images") or []
                if isinstance(images, list):
                    images_str = "|".join(images)
                else:
                    images_str = str(images)
                save_woocommerce_product({
                    "name": p.get("title", ""),
                    "sku": p.get("sku", ""),
                    "regular_price": str(p.get("price", "")),
                    "description": p.get("description", "") or "",
                    "categories": p.get("category", ""),
                    "brand": p.get("brand", ""),
                    "source_url": p.get("source_url", ""),
                    "images": images_str,
                    "stock_status": "instock",
                    "site_id": site_id,
                    "extra_data": {"currency": p.get("currency", "USD")},
                })
                count += 1
            # Also sync to static_site_products for site HTML regeneration
            try:
                mapped = [{k: p.get(k, "") for k in ["title","description","price","currency","images","image_url","category","brand","sku","source_url"]} for p in products]
                import_products_to_site(site_id, mapped)
                _regenerate_static_site_html(None, site_id)
            except Exception as re:
                logger.warning(f"Regenerate after CSV import failed: {re}")
            return jsonify({"code": 200, "data": {"imported": count, "total": len(products)}})

        if "file" not in request.files:
            return jsonify({"code": 400, "message": "请上传CSV文件"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.lower().endswith(".csv"):
            return jsonify({"code": 400, "message": "仅支持CSV文件"}), 400

        try:
            import csv, io
            content = file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
        except Exception as e:
            return jsonify({"code": 400, "message": f"CSV解析失败: {str(e)[:100]}"}), 400

        if not rows:
            return jsonify({"code": 400, "message": "CSV文件为空"}), 400

        # Parse WooCommerce CSV fields
        products = []
        for row in rows:
            title = (row.get("Name") or row.get("Title") or row.get("Product Name") or "").strip()
            if not title:
                continue

            price_str = (row.get("Regular price") or row.get("Price") or "0").strip().replace("$", "").replace(",", "")
            try:
                price = float(price_str) if price_str else 0
            except ValueError:
                price = 0

            # Parse categories
            cats_raw = row.get("Categories", "")
            cats = [c.strip() for c in cats_raw.replace("Category1:", "").replace("Category2:", "").replace("Category3:", "").split(",") if c.strip() and not c.strip().startswith("Category")]
            category = cats[-1] if cats else ""

            # Parse tags into attributes
            tags_raw = row.get("Tags", "")
            attrs = {}
            for tag in tags_raw.split(","):
                tag = tag.strip()
                if ":" in tag:
                    k, v = tag.split(":", 1)
                    attrs[k.strip()] = v.strip()

            # Parse images
            images_raw = row.get("Images", "")
            images = [img.strip() for img in images_raw.split(",") if img.strip() and ("http" in img)]

            products.append({
                "title": title,
                "sku": (row.get("SKU") or "").strip(),
                "description": (row.get("Description") or row.get("Short description") or "").strip()[:5000],
                "price": price,
                "category": category,
                "brand": attrs.get("Corporate Sub Brand", attrs.get("Brand", "")),
                "color": attrs.get("color", ""),
                "material": attrs.get("Material Desc", ""),
                "images": images,
                "image_url": images[0] if images else "",
                "source_url": f"https://{site.get('url', '')}/products/{row.get('SKU', '')}",
            })

        # If action=import, save to DB and regenerate
        action = request.form.get("action", "preview")
        if action == "import":
            mapped = []
            for p in products:
                mapped.append({
                    "title": p["title"],
                    "description": p["description"],
                    "price": str(p["price"]),
                    "currency": "USD",
                    "images": p["images"],
                    "image_url": p["image_url"],
                    "category": p["category"],
                    "brand": p["brand"],
                    "sku": p["sku"],
                    "source_url": p["source_url"],
                })
            count = import_products_to_site(site_id, mapped)
            try:
                _regenerate_static_site_html(None, site_id)
            except Exception as re:
                logger.warning(f"Regenerate after CSV import failed: {re}")
            return jsonify({"code": 200, "data": {"imported": count, "total": len(products)}})

        return jsonify({"code": 200, "data": {"products": products, "total": len(products)}})

    @app.route("/api/sites/<int:site_id>/static-products/import", methods=["POST"])
    @jwt_required()
    def import_site_products(site_id):
        """Bulk import products from screening results to a static site.
        Accepts a list of product objects. After import, regenerates the site HTML
        and feed.xml to include the new products.
        """
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        if site.get("site_type") != "static":
            return jsonify({"code": 400, "message": "仅支持静态站点"}), 400

        data = request.get_json(silent=True) or {}
        products = data.get("products", [])
        if not products:
            return jsonify({"code": 400, "message": "未提供产品数据"}), 400

        try:
            count = import_products_to_site(site_id, products)
            return jsonify({
                "code": 200,
                "message": f"成功导入 {count} 个产品",
                "data": {"imported": count},
            })
        except Exception as e:
            logger.error(f"Import products error: {traceback.format_exc()}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/static-products/<int:product_id>", methods=["PUT"])
    @jwt_required()
    def update_site_product(product_id):
        """Update a static site product."""
        data = request.get_json(silent=True) or {}
        product = update_static_site_product(product_id, data)
        if not product:
            return jsonify({"code": 404, "message": "产品不存在"}), 404
        return jsonify({"code": 200, "data": product})

    @app.route("/api/static-products/<int:product_id>", methods=["DELETE"])
    @jwt_required()
    def delete_site_product(product_id):
        """Delete a static site product and regenerate the site."""
        # Get site_id before deleting
        product = get_static_site_product(product_id)
        site_id = product["site_id"] if product else None
        deleted = delete_static_site_product(product_id)
        if not deleted:
            return jsonify({"code": 404, "message": "产品不存在"}), 404
        # Regenerate site after product removal
        if site_id:
            try:
                _regenerate_static_site_html(None, site_id)
            except Exception as e:
                logger.warning(f"Regenerate after delete failed: {e}")
        return jsonify({"code": 200, "message": "已删除，站点已更新"})

    @app.route("/api/sites/<int:site_id>/regenerate-static", methods=["POST"])
    @jwt_required()
    def regenerate_static_site(site_id):
        """Regenerate static site HTML after product changes.
        Rebuilds index.html (product grid) and product detail pages,
        then uploads to 1Panel.
        """
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        if site.get("site_type") != "static":
            return jsonify({"code": 400, "message": "仅支持静态站点"}), 400

        try:
            _regenerate_static_site_html(None, site_id)
            # Also regenerate feed
            try:
                _generate_static_feed(site_id, site)
            except Exception:
                pass
            products = list_static_site_products(site_id)
            return jsonify({
                "code": 200,
                "message": f"站点已重新生成 ({len(products)} 个产品)",
            })
        except Exception as e:
            logger.error(f"Regenerate static site error: {traceback.format_exc()}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- WordPress Batch Creation ----

    @app.route("/api/wordpress/batch-create", methods=["POST"])
    @jwt_required()
    def batch_create_wordpress():
        """Create multiple WordPress sites in batch via 1Panel.

        Workflow:
        1. For each domain, create a database via 1Panel's /databases API
        2. Install WordPress with PANEL_DB_HOST='mariadb' (1Panel DB name)
        3. 1Panel auto-resolves DB host address and sets PANEL_DB_PORT
        4. If allowPort wasn't applied during install, fix via update_installed API
        """
        try:
            data = request.get_json(silent=True) or {}
            domains = data.get("domains", [])
            if not domains:
                return jsonify({"code": 400, "message": "未提供域名"}), 400

            global_cfg = get_global_config()
            try:
                default_admin = data.get("admin_name") or json.loads(global_cfg.get("default_admin_name", '"admin"'))
            except (json.JSONDecodeError, TypeError):
                default_admin = data.get("admin_name", "admin")
            default_password = data.get("admin_password") or global_cfg.get("default_admin_password", "")
            tag = data.get("tag", "")
            security_id = data.get("security_id", "")
            # Auto-increment security_id if empty
            if not security_id:
                try:
                    conn = get_db()
                    row = conn.execute("SELECT MAX(CAST(security_id AS INTEGER)) FROM sites WHERE security_id GLOB '[0-9]*'").fetchone()
                    conn.close()
                    max_id = row[0] if row and row[0] else 0
                    security_id = str(max_id + 1)
                except Exception:
                    security_id = "1"
            http_username = data.get("http_username", "")
            http_password = data.get("http_password", "")
            verify_cert = data.get("verify_certificate", True)
            ssl_version = data.get("ssl_version", "auto")
            # Auto-install ALL enabled themes and plugins (not user-selected)
            plugin_ids = [p["id"] for p in get_enabled_plugins()]
            theme_ids = _scan_theme_zips()

            # Get WordPress app info
            app_resp = _get_panel_client().get_app("wordpress")
            if app_resp.get("code") != 200:
                return jsonify({"code": 500, "message": f"从1Panel获取WordPress应用信息失败: {app_resp.get('message', '')}"}), 500

            app_data = app_resp.get("data", {})
            if not app_data:
                return jsonify({"code": 500, "message": "WordPress应用数据为空"}), 500
            app_id = app_data.get("id")
            if not app_id:
                return jsonify({"code": 500, "message": "无法获取WordPress应用ID"}), 500
            versions = app_data.get("versions", [])
            version = versions[0] if versions else "6.9.4"

            # Get app detail for the latest version
            detail_resp = _get_panel_client().get_app_detail(app_id, version)
            if detail_resp.get("code") != 200:
                return jsonify({"code": 500, "message": f"获取WordPress应用详情失败: {detail_resp.get('message', '')}"}), 500

            detail_data = detail_resp.get("data", {})
            if not detail_data:
                return jsonify({"code": 500, "message": "WordPress应用详情数据为空"}), 500
            app_detail_id = detail_data.get("id")

            # Get database service name (default: mariadb)
            db_service = data.get("db_service") or global_cfg.get("db_service", "mariadb")

            # Verify the database service exists in 1Panel
            db_installed_resp = _get_panel_client().search_installed_apps(name=db_service)
            if db_installed_resp.get("code") != 200 or not db_installed_resp.get("data", {}).get("items", []):
                return jsonify({"code": 500, "message": f"数据库服务 {db_service} 未安装或未运行，请先在1Panel中安装MariaDB"}), 500

            results = []
            # Default base port: max existing site port + 1, min 8081
            if "base_port" in data and data["base_port"] is not None:
                base_port = int(data["base_port"])
            else:
                conn = get_db()
                row = conn.execute("SELECT MAX(port) as max_port FROM sites").fetchone()
                conn.close()
                base_port = max((row["max_port"] or 8080) + 1, 8081)
            used_ports = set()

            # Get ALL currently used ports from 1Panel
            try:
                installed_resp = _get_panel_client().search_installed_apps(page=1, page_size=200)
                if installed_resp.get("code") == 200:
                    for item in installed_resp.get("data", {}).get("items", []):
                        if item.get("httpPort"):
                            used_ports.add(item["httpPort"])
                        if item.get("httpsPort"):
                            used_ports.add(item["httpsPort"])
            except Exception:
                pass

            # Also scan host ports for safety
            try:
                port_scan = subprocess.run(
                    ["ss", "-tlnp", "-H"], capture_output=True, text=True, timeout=5
                )
                if port_scan.returncode == 0:
                    for line in port_scan.stdout.strip().split("\n"):
                        parts = line.split()
                        for p in parts:
                            if ":" in p:
                                port_str = p.rsplit(":", 1)[-1]
                                try:
                                    used_ports.add(int(port_str))
                                except ValueError:
                                    pass
            except Exception:
                pass

            def find_available_port(start):
                port = start
                while port in used_ports:
                    port += 1
                used_ports.add(port)
                return port

            for i, domain in enumerate(domains):
                domain = domain.strip()
                if not domain:
                    continue

                alias = domain
                site_name = domain
                port = find_available_port(base_port)

                # Generate DB credentials
                import string
                import random
                db_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
                db_name = f"wp_{db_suffix}"
                db_user = f"wp_{db_suffix}"
                db_pass = "".join(random.choices(string.ascii_letters + string.digits, k=16))

                # Resolve CloakBrowser profile from brand kit if fingerprint is enabled
                cloakbrowser_profile = None
                brand_kit_id = data.get("brand_kit_id")
                if brand_kit_id and get_global_config().get("fingerprint_enabled", "false") == "true":
                    kit = get_brand_kit(int(brand_kit_id))
                    if kit and kit.get("cloakbrowser_profile_name"):
                        cloakbrowser_profile = kit["cloakbrowser_profile_name"]

                # Create a placeholder site in local DB immediately
                site = create_site({
                    "site_name": site_name,
                    "url": f"http://{domain}",
                    "admin_name": default_admin,
                    "admin_password": default_password,
                    "tag": tag,
                    "security_id": security_id,
                    "http_username": http_username,
                    "http_password": http_password,
                    "verify_certificate": verify_cert,
                    "ssl_version": ssl_version,
                    "port": port,
                    "db_name": db_name,
                    "db_service": db_service,
                    "created_by": get_current_user_id(),
                    "cloakbrowser_profile_name": cloakbrowser_profile,
                })
                site_id_for_bg = site["id"] if site else 0

                # Initialize bg task
                bg_task_id = create_bg_task(site_id_for_bg, "wp_install", status="installing",
                               message="1Panel正在创建数据库...")

                # === Sync DNS creation (before background thread) ===
                import sys as _sys
                dns_created_sync = False
                # Resolve panel environment for CF account binding and target IP.
                # If user selected a CF account in wizard, use the env bound to it.
                env = get_environment_by_cf_account(data.get("cf_account_id"))
                if not env:
                    try:
                        env = get_user_panel_environment(get_current_user_id())
                    except Exception:
                        env = None
                env_cf_account_id = data.get("cf_account_id") or (env.get("cf_account_id") if env else None)
                ip_sync = (env.get("host") if env else None) or config.PANEL_SERVER_IP
                _sys.stderr.write(f"[DNS-SYNC] domain={domain} cf_account_id={data.get('cf_account_id')} env_cf={env_cf_account_id} ip={ip_sync}\n")
                _sys.stderr.flush()
                try:
                    cf_creds = _get_cf_credentials(account_id=env_cf_account_id)
                    _sys.stderr.write(f"[DNS-SYNC] cf_creds has_token={bool(cf_creds.get('api_token'))}\n")
                    _sys.stderr.flush()
                    if cf_creds.get("api_token"):
                        from cloudflare_client import CloudflareClient
                        cf_sync = CloudflareClient(api_token=cf_creds["api_token"])
                        domain_parts = domain.rstrip(".").split(".")
                        root_domain_s = ".".join(domain_parts[-2:]) if len(domain_parts) >= 2 else domain
                        zone_sync = cf_sync.find_zone_by_name(root_domain_s)
                        _sys.stderr.write(f"[DNS-SYNC] root_domain={root_domain_s} zone_found={zone_sync is not None}\n")
                        _sys.stderr.flush()
                        if zone_sync:
                            zone_id = zone_sync["id"]
                            ssl_resp = cf_sync.set_ssl_mode(zone_id, "flexible")
                            logger.info("Sync DNS: SSL mode set to flexible for zone %s: %s",
                                        root_domain_s, ssl_resp.get("success"))
                            dns_resp = cf_sync.create_dns_record(
                                zone_id, "A", domain, ip_sync, proxied=True, ttl=1)
                            _sys.stderr.write(f"[DNS-SYNC] create_dns_record result: success={dns_resp.get('success')} errors={dns_resp.get('errors')}\n")
                            _sys.stderr.flush()
                            if dns_resp.get("success"):
                                dns_data = dns_resp.get("result", {})
                                update_site_fields(site_id_for_bg, {
                                    "cf_zone_id": zone_id,
                                    "cf_dns_record_id": dns_data.get("id", ""),
                                })
                                dns_created_sync = True
                                update_bg_task(bg_task_id, status="installing",
                                               message=f"DNS已创建: {domain} → {ip_sync}")
                                logger.info(f"Sync DNS created: {domain} A → {ip_sync}")
                            else:
                                # Record may already exist (site deleted + recreated)
                                errors = dns_resp.get("errors", [])
                                if any(e.get("code") == 81058 for e in errors):
                                    logger.info("Sync DNS: record exists for %s, checking...", domain)
                                    existing = cf_sync.list_dns_records(zone_id, "A", domain)
                                    if existing.get("success") and existing.get("result"):
                                        rec = existing["result"][0]
                                        if rec.get("content") == ip_sync:
                                            logger.info("Sync DNS: existing record %s → %s is correct",
                                                        domain, ip_sync)
                                            update_site_fields(site_id_for_bg, {
                                                "cf_zone_id": zone_id,
                                                "cf_dns_record_id": rec.get("id", ""),
                                            })
                                            dns_created_sync = True
                                        else:
                                            logger.info("Sync DNS: updating %s %s → %s",
                                                        domain, rec.get("content"), ip_sync)
                                            upd = cf_sync.update_dns_record(zone_id, rec["id"],
                                                {"type": "A", "name": domain, "content": ip_sync,
                                                 "proxied": True, "ttl": 1})
                                            if upd.get("success"):
                                                update_site_fields(site_id_for_bg, {
                                                    "cf_zone_id": zone_id,
                                                    "cf_dns_record_id": rec.get("id", ""),
                                                })
                                                dns_created_sync = True
                                                logger.info("Sync DNS updated: %s → %s", domain, ip_sync)
                                            else:
                                                logger.warning("Sync DNS update failed for %s: %s",
                                                               domain, upd.get("errors"))
                                    else:
                                        logger.warning("Sync DNS: could not find existing record for %s", domain)
                                else:
                                    logger.warning(f"Sync DNS API error for {domain}: {errors}")
                        else:
                            logger.info("Sync DNS: no zone for %s, attempting to add...", root_domain_s)
                            new_zone = cf_sync.add_zone(root_domain_s)
                            if new_zone:
                                zone_id = new_zone["id"]
                                cf_sync.set_ssl_mode(zone_id, "flexible")
                                logger.info("Sync DNS: zone %s created (id=%s), SSL=flexible", root_domain_s, zone_id)
                                dns_resp = cf_sync.create_dns_record(
                                    zone_id, "A", domain, ip_sync, proxied=True, ttl=1)
                                if dns_resp.get("success"):
                                    dns_data = dns_resp.get("result", {})
                                    update_site_fields(site_id_for_bg, {
                                        "cf_zone_id": zone_id,
                                        "cf_dns_record_id": dns_data.get("id", ""),
                                    })
                                    dns_created_sync = True
                                    update_bg_task(bg_task_id, status="installing",
                                                   message=f"DNS zone+record: {domain} → {ip_sync}")
                                    logger.info("Sync DNS: zone+record created for %s A → %s", domain, ip_sync)
                                else:
                                    logger.warning("Sync DNS: zone created but record failed for %s: %s", domain, dns_resp.get("errors"))
                            else:
                                logger.warning("Sync DNS: could not add zone %s — domain may not be on Cloudflare", root_domain_s)
                    else:
                        _sys.stderr.write(f"[DNS-SYNC] NO API TOKEN for account_id={env_cf_account_id}\n")
                        _sys.stderr.flush()
                        logger.warning(f"Sync DNS: no Cloudflare token configured")
                except Exception as dns_sync_e:
                    _sys.stderr.write(f"[DNS-SYNC] EXCEPTION: {dns_sync_e}\n")
                    _sys.stderr.flush()
                    logger.warning(f"Sync DNS exception for {domain}: {dns_sync_e}")

                # ---- Background thread: full deployment pipeline ----
                def _bg_deploy(task_id, sid, s_alias, s_domain, s_port, s_db_name, s_db_user, s_db_pass,
                                s_app_detail_id, s_app_id, s_db_service, s_admin, s_password, s_plugin_ids,
                                s_theme_ids, s_group_id, s_panel_host, s_panel_port, s_panel_api_key):
                    """Full deployment pipeline in background with real-time status updates."""
                    # Push Flask application context for this thread
                    with app.app_context():
                        _bg_deploy_inner(task_id, sid, s_alias, s_domain, s_port, s_db_name, s_db_user, s_db_pass,
                                         s_app_detail_id, s_app_id, s_db_service, s_admin, s_password, s_plugin_ids,
                                         s_theme_ids, s_group_id, s_panel_host, s_panel_port, s_panel_api_key)

                def _bg_deploy_inner(task_id, sid, s_alias, s_domain, s_port, s_db_name, s_db_user, s_db_pass,
                                     s_app_detail_id, s_app_id, s_db_service, s_admin, s_password, s_plugin_ids,
                                     s_theme_ids, s_group_id, s_panel_host, s_panel_port, s_panel_api_key):
                    """Inner deployment logic (runs inside Flask app context)."""
                    # Use the operator's panel environment (captured before thread spawn — JWT is lost in background thread)
                    _get_panel_client = lambda: OnePanelClient(host=s_panel_host, port=s_panel_port, api_key=s_panel_api_key)
                    container_name = None
                    app_install_id = None
                    panel_website_id = None

                    def _rollback_deploy(db_name, app_install_id_to_delete=None):
                        """清理部署失败时已创建的资源（数据库和应用）。"""
                        if app_install_id_to_delete:
                            try:
                                _get_panel_client().operate_installed(
                                    app_install_id_to_delete, "delete",
                                    force_delete=True, delete_backup=True, delete_db=False,
                                )
                                logger.info(f"Rollback: deleted app install {app_install_id_to_delete}")
                            except Exception as re:
                                logger.warning(f"Rollback: failed to delete app: {re}")
                        if db_name:
                            try:
                                db_resp = _get_panel_client().search_databases(name=db_name)
                                if db_resp.get("code") == 200:
                                    db_items = (db_resp.get("data") or {}).get("items") or []
                                    for d in db_items:
                                        if d.get("name") == db_name:
                                            _get_panel_client().delete_database(
                                                d.get("id"), db_type=s_db_service,
                                                delete_user=True, force_delete=True,
                                            )
                                            logger.info(f"Rollback: deleted database {db_name}")
                                            break
                            except Exception as re:
                                logger.warning(f"Rollback: failed to delete DB: {re}")

                    try:
                        # === Step 1: Create database ===
                        update_bg_task(task_id, status="installing", message="1Panel正在创建数据库...")
                        db_created = False
                        try:
                            db_resp = _get_panel_client().create_database(
                                name=s_db_name, db_type=s_db_service, username=s_db_user,
                                password=s_db_pass, permission="%", format_str="utf8mb4",
                            )
                            db_created = db_resp.get("code") == 200
                        except Exception as e:
                            logger.warning(f"DB creation failed for {s_domain}: {e}")

                        if not db_created:
                            update_bg_task(task_id, status="failed",
                                           message=f"创建数据库 {s_db_name} 失败，请检查1Panel数据库服务")
                            return

                        # === Step 2: Create deployment website (primary: one-step appType=new) ===
                        install_params = {
                            "PANEL_DB_TYPE": s_db_service,
                            "PANEL_DB_HOST": s_db_service,
                            "PANEL_DB_NAME": s_db_name,
                            "PANEL_DB_USER": s_db_user,
                            "PANEL_DB_USER_PASSWORD": s_db_pass,
                            "PANEL_APP_PORT_HTTP": str(s_port),
                        }

                        website_result = None

                        # --- Primary path: one-step create_website(appType=new) ---
                        # This creates both the WordPress app AND the deployment website in one call.
                        # 1Panel's OpenResty reverse proxy is properly configured this way.
                        update_bg_task(task_id, status="installing",
                                       message="1Panel正在安装WordPress并创建网站...")
                        for _attempt in range(3):
                            try:
                                logger.info(f"Step2: One-step create website+app for {s_domain} (attempt {_attempt+1}/3)")
                                website_result = _get_panel_client().create_website(
                                    primary_domain=s_domain,
                                    alias=s_alias,
                                    app_type="new",
                                    app_detail_id=s_app_detail_id,
                                    app_id=s_app_id,
                                    app_install_params=install_params,
                                    services={s_db_service: s_db_service},
                                    website_group_id=s_group_id,
                                    enable_ipv6=False,
                                    port=s_port,
                                )
                                logger.info(f"Step2: create_website(new) response: code={website_result.get('code')}, message={website_result.get('message','')[:200]}")
                                if website_result.get("code") == 200:
                                    break
                                if "标识已存在" in str(website_result.get("message", "")):
                                    unique_alias = f"{s_alias}-{_attempt+1}"
                                    logger.warning(f"Step2: Alias conflict, trying {unique_alias}")
                                    website_result = _get_panel_client().create_website(
                                        primary_domain=s_domain,
                                        alias=unique_alias,
                                        app_type="new",
                                        app_detail_id=s_app_detail_id,
                                        app_id=s_app_id,
                                        app_install_params=install_params,
                                        services={s_db_service: s_db_service},
                                        website_group_id=s_group_id,
                                        enable_ipv6=False,
                                        port=s_port,
                                    )
                                    logger.info(f"Step2: Retry alias={unique_alias}: code={website_result.get('code')}")
                                    if website_result.get("code") == 200:
                                        s_alias = unique_alias
                                        break
                            except Exception as e:
                                logger.error(f"Step2: One-step create exception (attempt {_attempt+1}): {e}")
                                website_result = {"code": 500, "message": str(e)[:200]}
                            if _attempt < 2:
                                time.sleep(5)

                        if website_result and website_result.get("code") == 200:
                            time.sleep(8)
                            # Find the website and app IDs from search_websites response
                            try:
                                ws = _get_panel_client().search_websites(name=s_domain)
                                if ws.get("code") == 200:
                                    items = ws.get("data", {}).get("items") or []
                                    for w in items:
                                        if w.get("alias") == s_alias or w.get("primaryDomain") == s_domain:
                                            panel_website_id = w.get("id")
                                            app_install_id = w.get("appInstallId")
                                            container_name = w.get("containerName", "") or w.get("container", "")
                                            logger.info(f"Step2: Found website id={panel_website_id}, appInstallId={app_install_id}, container={container_name} for {s_domain}")
                                            break
                                if app_install_id and not container_name:
                                    try:
                                        app_params = _get_panel_client().get_installed_params(app_install_id)
                                        if app_params.get("code") == 200:
                                            d = app_params.get("data") or {}
                                            container_name = d.get("container") or d.get("containerName") or ""
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.warning(f"Step2: search_websites failed: {e}")

                            if not app_install_id:
                                try:
                                    new_installed = _get_panel_client().search_installed_apps(name=s_alias)
                                    if new_installed.get("code") == 200:
                                        new_app = next(
                                            (a for a in new_installed.get("data", {}).get("items", [])
                                             if a.get("name") == s_alias), None,
                                        )
                                        if new_app:
                                            app_install_id = new_app.get("id")
                                            container_name = new_app.get("container", "")
                                except Exception:
                                    pass

                            if panel_website_id:
                                update_bg_task(task_id, status="deploying",
                                               message="1Panel已创建网站和WordPress应用，正在等待就绪...")
                                # Always create explicit nginx proxy config.
                                # create_website's auto-generated nginx may not forward sub-paths
                                # (e.g. /wp-login.php) correctly on some 1Panel versions.
                                try:
                                    nginx_resp = _get_panel_client().create_nginx_proxy_config(
                                        alias=s_alias, domain=s_domain, port=s_port,
                                    )
                                    if nginx_resp.get("code") == 200:
                                        logger.info(f"Step2: nginx proxy config created for {s_domain}")
                                    else:
                                        logger.warning(f"Step2: nginx proxy config issue: {nginx_resp.get('message','')[:100]}")
                                except Exception as ne:
                                    logger.warning(f"Step2: nginx proxy config failed: {ne}")

                        # --- Fallback path: install_app + create_website(appType=installed) ---
                        if not (website_result and website_result.get("code") == 200 and panel_website_id):
                            logger.warning(f"Step2: create_website(new) failed, falling back to two-step install+deploy")
                            update_bg_task(task_id, status="installing",
                                           message="1Panel正在安装WordPress应用(两步模式)...")
                            try:
                                install_resp = _get_panel_client().install_app(
                                    app_detail_id=s_app_detail_id, name=s_alias,
                                    params=install_params, services={s_db_service: s_db_service},
                                    advanced=True, allow_port=True,
                                )
                            except Exception as e:
                                logger.error(f"Step2: install_app exception: {e}")
                                install_resp = {"code": 500, "message": str(e)[:200]}

                            if install_resp.get("code") == 200:
                                logger.info(f"Step2(fallback): install_app succeeded for {s_alias}")
                                time.sleep(5)

                                try:
                                    new_installed = _get_panel_client().search_installed_apps(name=s_alias)
                                    if new_installed.get("code") == 200:
                                        new_app = next(
                                            (a for a in new_installed.get("data", {}).get("items", [])
                                             if a.get("name") == s_alias), None,
                                        )
                                        if new_app:
                                            app_install_id = new_app.get("id")
                                            container_name = new_app.get("container", "")
                                            logger.info(f"Step2(fallback): Found installed app id={app_install_id}")
                                except Exception as e:
                                    logger.warning(f"Step2(fallback): search_installed_apps failed: {e}")

                                if app_install_id:
                                    update_bg_task(task_id, status="deploying",
                                                   message="1Panel正在创建网站并配置反向代理...")
                                    for _attempt2 in range(3):
                                        try:
                                            logger.info(f"Step2(fallback): Creating deployment website for {s_domain} (attempt {_attempt2+1}/3)")
                                            website_result = _get_panel_client().create_website(
                                                primary_domain=s_domain,
                                                alias=s_alias,
                                                app_type="installed",
                                                app_install_id=app_install_id,
                                                website_group_id=s_group_id,
                                                enable_ipv6=False,
                                                port=s_port,
                                            )
                                            logger.info(f"Step2(fallback): create_website(installed) response: code={website_result.get('code')}")
                                            if website_result.get("code") == 200:
                                                break
                                            if "标识已存在" in str(website_result.get("message", "")):
                                                unique_alias = f"{s_alias}-1"
                                                logger.warning(f"Step2(fallback): Alias conflict, trying {unique_alias}")
                                                website_result = _get_panel_client().create_website(
                                                    primary_domain=s_domain,
                                                    alias=unique_alias,
                                                    app_type="installed",
                                                    app_install_id=app_install_id,
                                                    website_group_id=s_group_id,
                                                    enable_ipv6=False,
                                                    port=s_port,
                                                )
                                                if website_result.get("code") == 200:
                                                    s_alias = unique_alias
                                                    break
                                        except Exception as e:
                                            logger.error(f"Step2(fallback): create_website(installed) exception: {e}")
                                        time.sleep(5)

                                    if website_result and website_result.get("code") == 200:
                                        time.sleep(3)
                                        try:
                                            ws = _get_panel_client().search_websites(name=s_domain)
                                            if ws.get("code") == 200:
                                                items = ws.get("data", {}).get("items") or []
                                                for w in items:
                                                    if w.get("alias") == s_alias or w.get("primaryDomain") == s_domain:
                                                        panel_website_id = w.get("id")
                                                        logger.info(f"Step2(fallback): Found website id={panel_website_id} for {s_domain}")
                                                        break
                                        except Exception as e:
                                            logger.warning(f"Step2(fallback): search_websites failed: {e}")

                                        update_bg_task(task_id, status="deploying",
                                                       message="1Panel已部署网站(一键部署)，正在等待WordPress就绪...")
                                else:
                                    logger.warning(f"Step2(fallback): Could not find app_install_id after install")
                            else:
                                logger.warning(f"Step2(fallback): install_app failed ({install_resp.get('message','')[:100]})")

                        # --- Last resort: manual nginx proxy config ---
                        if not (website_result and website_result.get("code") == 200 and panel_website_id):
                            error_msg = website_result.get('message', '未知错误') if website_result else '无响应'
                            logger.warning(f"Step2: All paths failed ({error_msg}), falling back to nginx proxy")
                            update_bg_task(task_id, status="deploying",
                                           message="1Panel网站API不可用，正在手动配置nginx反向代理...")
                            try:
                                nginx_resp = _get_panel_client().create_nginx_proxy_config(
                                    alias=s_alias, domain=s_domain, port=s_port,
                                )
                                if nginx_resp.get("code") == 200:
                                    logger.info(f"Step2: nginx proxy config created for {s_domain}")
                                else:
                                    logger.warning(f"Step2: nginx proxy config partial: {nginx_resp.get('message','')[:100]}")
                            except Exception as e:
                                logger.error(f"Step2: nginx proxy creation failed: {e}")
                                update_bg_task(task_id, status="failed",
                                               message=f"创建nginx反向代理失败: {str(e)[:80]}")
                                _rollback_deploy(s_db_name, app_install_id)
                                return

                        # Update local DB with panel IDs
                        try:
                            update_site_fields(sid, {
                                "panel_website_id": panel_website_id,
                                "panel_app_install_id": app_install_id,
                                "panel_app_detail_id": s_app_detail_id,
                                "nginx_alias": s_alias,
                            })
                        except Exception:
                            pass

                        # === Step 3: Resolve container_name & prepare for WP install ===
                        from config import config as app_config
                        wp_host = s_panel_host or app_config.PANEL_SERVER_IP

                        # Fix nginx client_max_body_size for large file uploads (theme zip is ~37MB)
                        if panel_website_id or s_alias:
                            try:
                                _nginx_fix_body_size(_get_panel_client(), s_alias, panel_website_id)
                            except Exception:
                                pass

                        # Resolve container_name from 1Panel API
                        if not container_name and app_install_id:
                            try:
                                app_params = _get_panel_client().get_installed_params(app_install_id)
                                if app_params.get("code") == 200:
                                    d = app_params.get("data") or {}
                                    container_name = d.get("containerName") or d.get("container") or ""
                            except Exception:
                                pass
                        if not container_name:
                            try:
                                inst = _get_panel_client().search_installed_apps(name=s_alias)
                                if inst.get("code") == 200:
                                    for a in (inst.get("data") or {}).get("items") or []:
                                        if a.get("name") == s_alias:
                                            container_name = a.get("containerName") or a.get("container") or ""
                                            break
                            except Exception:
                                pass
                        logger.info("Resolved container_name=%s for %s", container_name or "(none)", s_domain)

                        result = auto_install_wordpress(
                            container_name=container_name or "",
                            site_url=f"http://{s_domain}",
                            site_title=s_domain,
                            admin_user=s_admin,
                            admin_password=s_password,
                            admin_email=f"admin@{s_domain}",
                            port=s_port,
                            ip_url=f"http://{wp_host}:{s_port}",
                        )

                        if result.get("success"):
                            # === Step 6: Install theme FIRST ===
                            wp_url = f"http://{s_domain}"
                            install_url = f"http://{wp_host}:{s_port}"
                            msg_parts = []

                            if s_theme_ids:
                                update_bg_task(task_id, status="installing",
                                               message=f"正在安装 {len(s_theme_ids)} 个主题...")
                                theme_results = install_themes_to_site(
                                    wp_url, s_admin, s_password, s_theme_ids, ip_url=install_url)
                                for tr in theme_results:
                                    logger.info("Theme install: %s → %s (%s)",
                                                tr.get("theme"), tr.get("status"), tr.get("message", ""))
                                ok = sum(1 for r in theme_results if r.get("status") == "success")
                                msg_parts.append(f"{ok}/{len(s_theme_ids)} 个主题")

                            # === Step 7: Apply post-install configs (MU plugin handles plugins+timeouts) ===
                            if s_plugin_ids or s_theme_ids:
                                try:
                                    _theme_slug = s_theme_ids[0][0] if s_theme_ids else "woodmart"
                                    _apply_post_install_configs(wp_url, s_admin, s_password,
                                                                 theme_slug=_theme_slug, ip_url=install_url)
                                    logger.info("Post-install configs applied for %s", s_domain)
                                except Exception as ce:
                                    logger.warning("Post-install configs failed for %s: %s", s_domain, ce)

                            final_msg = "部署完成！WordPress已安装"
                            if msg_parts:
                                final_msg += "，" + "，".join(msg_parts)
                            else:
                                final_msg += " (1Panel/OpenResty)"
                            logger.info("Deployment complete for %s: %s", s_domain, final_msg)
                            update_bg_task(task_id, status="installed", message=final_msg)
                        else:
                            update_bg_task(task_id, status="failed",
                                           message=f"WordPress初始化失败: {result.get('message', '未知错误')[:80]}")

                    except Exception as e:
                        update_bg_task(task_id, status="failed", message=f"部署异常: {str(e)[:100]}")
                        _rollback_deploy(s_db_name, app_install_id)
                        logger.error(f"BG deploy error for {s_domain}: {e}")

                # Get group ID before starting bg thread
                # Use website_group_id from frontend request, or auto-detect
                website_group_id = data.get("website_group_id")
                if website_group_id:
                    group_id = website_group_id
                else:
                    try:
                        group_id = _get_panel_client().ensure_website_group()
                    except Exception:
                        group_id = 1

                # Capture panel client params BEFORE background thread (JWT is lost in thread).
                # Use env resolved above (may be from cf_account_id binding).
                if env:
                    _user_pc = OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"])
                else:
                    _user_pc = _get_panel_client()
                _panel_host, _panel_port, _panel_api_key = _user_pc.host, _user_pc.port, _user_pc.api_key
                bg_thread = threading.Thread(
                    target=_bg_deploy,
                    args=(bg_task_id, site_id_for_bg, alias, domain, port, db_name, db_user, db_pass,
                          app_detail_id, app_id, db_service, default_admin, default_password,
                          plugin_ids, theme_ids, group_id, _panel_host, _panel_port, _panel_api_key),
                    daemon=True,
                )
                bg_thread.start()
                logger.info(f"Started background deployment for {domain} (site_id={site_id_for_bg}, task_id={bg_task_id})")

                results.append({
                    "domain": domain,
                    "status": "success",
                    "port": port,
                    "site_id": site["id"] if site else None,
                    "wp_install_status": "installing",
                    "wp_install_message": f"DNS已创建: {domain} → {ip_sync}" if dns_created_sync else "DNS未创建(需配置Cloudflare)，继续安装WordPress...",
                    "dns_created": dns_created_sync,
                })

            success_count = sum(1 for r in results if r["status"] == "success")
            error_count = sum(1 for r in results if r["status"] == "error")

            return jsonify({
                "code": 200,
                "data": {
                    "results": results,
                    "total": len(results),
                    "success": success_count,
                    "error": error_count,
                },
            })
        except Exception as e:
            logger.error(f"Batch create failed: {e}")
            return jsonify({"code": 500, "message": f"批量创建失败: {str(e)[:100]}"}), 500

    # ---- Global Config ----

    @app.route("/api/config", methods=["GET"])
    @jwt_required()
    def get_config():
        try:
            cfg = get_global_config()
            # Parse JSON values
            for key in ["default_plugins", "default_themes"]:
                if key in cfg:
                    try:
                        cfg[key] = json.loads(cfg[key])
                    except (json.JSONDecodeError, TypeError):
                        cfg[key] = []
            return jsonify({"code": 200, "data": cfg})
        except Exception as e:
            logger.error(f"Failed to get config: {e}")
            return jsonify({"code": 500, "message": f"获取配置失败: {str(e)[:100]}"}), 500

    @app.route("/api/config", methods=["PUT"])
    @admin_required
    def save_config():
        try:
            data = request.get_json(silent=True) or {}
            for key, value in data.items():
                update_global_config(key, value)
            return jsonify({"code": 200, "message": "Config saved"})
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return jsonify({"code": 500, "message": f"保存配置失败: {str(e)[:100]}"}), 500

    # ---- DeepSeek API Configuration ----
    @app.route("/api/deepseek/verify", methods=["POST"])
    @jwt_required()
    def deepseek_verify():
        """Verify one or more DeepSeek API keys."""
        data = request.get_json(silent=True) or {}
        raw = (data.get("api_key") or "").strip()
        if not raw:
            return jsonify({"code": 400, "message": "API Key 不能为空"}), 400

        from services.api_key_rotator import resolve_keys
        keys = resolve_keys(raw)
        if not keys:
            return jsonify({"code": 400, "message": "API Key 不能为空"}), 400

        results = []
        all_ok = True
        for i, key in enumerate(keys):
            try:
                resp = http_requests.get(
                    "https://api.deepseek.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    models_data = resp.json()
                    model_count = len(models_data.get("data", []))
                    results.append({"index": i, "ok": True, "models": model_count})
                elif resp.status_code == 401:
                    results.append({"index": i, "ok": False, "error": "API Key 无效"})
                    all_ok = False
                else:
                    results.append({"index": i, "ok": False, "error": f"HTTP {resp.status_code}"})
                    all_ok = False
            except http_requests.exceptions.Timeout:
                results.append({"index": i, "ok": False, "error": "响应超时"})
                all_ok = False
            except http_requests.exceptions.ConnectionError:
                results.append({"index": i, "ok": False, "error": "无法连接"})
                all_ok = False
            except Exception as e:
                results.append({"index": i, "ok": False, "error": str(e)[:80]})
                all_ok = False

        if all_ok:
            update_global_config("deepseek_api_key", json.dumps(keys))
            return jsonify({
                "code": 200,
                "message": f"全部 {len(keys)} 个密钥验证通过",
                "data": {"results": results, "count": len(keys)},
            })
        else:
            failed = [r for r in results if not r["ok"]]
            return jsonify({
                "code": 400,
                "message": f"{len(failed)}/{len(keys)} 个密钥验证失败",
                "data": {"results": results},
            }), 400

    # ---- Crawlbase API Configuration ----
    @app.route("/api/crawlbase/verify", methods=["POST"])
    @jwt_required()
    def crawlbase_verify():
        """Verify one or more Crawlbase API tokens."""
        data = request.get_json(silent=True) or {}
        raw = (data.get("api_key") or "").strip()
        if not raw:
            return jsonify({"code": 400, "message": "API Token 不能为空"}), 400

        from services.api_key_rotator import resolve_keys
        keys = resolve_keys(raw)
        if not keys:
            return jsonify({"code": 400, "message": "API Token 不能为空"}), 400

        import requests
        results = []
        all_ok = True
        for i, key in enumerate(keys):
            try:
                resp = requests.get(
                    "https://api.crawlbase.com/?token=" + key + "&url=https://httpbin.org/ip",
                    timeout=30,
                )
                if resp.status_code == 200:
                    results.append({"index": i, "ok": True})
                elif resp.status_code in (401, 403):
                    results.append({"index": i, "ok": False, "error": "Token 无效"})
                    all_ok = False
                else:
                    results.append({"index": i, "ok": False, "error": f"HTTP {resp.status_code}"})
                    all_ok = False
            except Exception as e:
                results.append({"index": i, "ok": False, "error": str(e)[:80]})
                all_ok = False

        if all_ok:
            update_global_config("crawlbase_api_key", json.dumps(keys))
            return jsonify({
                "code": 200,
                "message": f"全部 {len(keys)} 个 Token 验证通过",
                "data": {"results": results, "count": len(keys)},
            })
        else:
            failed = [r for r in results if not r["ok"]]
            return jsonify({
                "code": 400,
                "message": f"{len(failed)}/{len(keys)} 个 Token 验证失败",
                "data": {"results": results},
            }), 400

    # ---- Google Merchant Center 自动化 ----

    @app.route("/api/sites/<int:site_id>/generate-feed", methods=["POST"])
    @jwt_required()
    def generate_google_feed(site_id):
        """Generate Google Shopping product feed XML for a site.
        For static sites: reads from local static_site_products table.
        For WordPress sites: uses WordPressAdminSession (legacy).
        """
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        # Static site: generate feed from local DB
        if site.get("site_type") == "static":
            return _generate_static_feed(site_id, site)

        # WordPress site (legacy): generate feed via WP API
        try:
            wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
            result = wp.generate_google_feed()
            code = 200 if result.get("success") else 500
            if result.get("success") and result.get("feed_url"):
                from models import update_site
                update_site(site_id, {"google_feed_url": result["feed_url"]})
            return jsonify({"code": code, "message": result.get("message", ""), "data": result}), code
        except Exception as e:
            logger.error(f"generate_feed error site={site_id}: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/verify-google-site", methods=["POST"])
    @jwt_required()
    def verify_google_site(site_id):
        """Inject Google Site Verification tag into the WordPress site."""
        data = request.get_json(silent=True) or {}
        verification_code = (data.get("verification_code") or "").strip()
        method = (data.get("method") or "meta").strip()
        if not verification_code:
            return jsonify({"code": 400, "message": "验证码不能为空"}), 400
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        try:
            wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
            result = wp.inject_google_verification(verification_code, method)
            code = 200 if result.get("success") else 500
            if result.get("success"):
                from models import update_site
                update_site(site_id, {
                    "google_verification_done": 1,
                    "google_verification_method": method,
                })
            return jsonify({"code": code, "message": result.get("message", ""), "data": result}), code
        except Exception as e:
            logger.error(f"verify_google_site error site={site_id}: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/inject-meta", methods=["POST"])
    @jwt_required()
    def inject_meta_tag(site_id):
        """Inject a custom meta tag into the site header (static: direct HTML, WP: via admin)."""
        data = request.get_json(silent=True) or {}
        meta_tag = (data.get("meta_tag") or "").strip()
        if not meta_tag:
            return jsonify({"code": 400, "message": "Meta标签不能为空"}), 400
        if not (meta_tag.startswith("<meta") and ">" in meta_tag):
            return jsonify({"code": 400, "message": "请输入有效的meta标签"}), 400
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        # Static site: directly modify index.html on 1Panel
        if site.get("site_type") == "static":
            try:
                env = get_user_panel_environment(site.get("created_by") or 1)
                if not env:
                    return jsonify({"code": 400, "message": "未找到1Panel环境配置"}), 400
                pc = OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"])
                nginx_alias = site.get("nginx_alias", "")
                site_dir = site.get("static_dir", "")
                if not nginx_alias:
                    return jsonify({"code": 400, "message": "站点缺少nginx别名"}), 400

                # Resolve file path
                if site_dir.startswith("/www/"):
                    remote_path = f"/opt/1panel/apps/openresty/openresty{site_dir}/index.html"
                elif site_dir.startswith("/opt/"):
                    remote_path = f"{site_dir}/index.html"
                else:
                    remote_path = f"/opt/1panel/apps/openresty/openresty/www/sites/{nginx_alias}/index/index.html"

                # Read current HTML
                content_resp = pc.read_file(remote_path)
                html = content_resp.get("data", {}).get("content", "") if content_resp.get("code") == 200 else ""
                if not html and isinstance(content_resp.get("data"), str):
                    html = content_resp["data"]

                if not html or "<html" not in html.lower():
                    logger.warning(f"inject_meta: empty or invalid HTML at {remote_path}, resp={str(content_resp)[:200]}")
                    return jsonify({"code": 500, "message": f"无法读取站点HTML ({remote_path})"}), 500

                # Inject meta tag before </head>
                if "</head>" in html:
                    html = html.replace("</head>", f"    {meta_tag}\n</head>")
                elif "<head>" in html:
                    html = html.replace("<head>", f"<head>\n    {meta_tag}")
                else:
                    return jsonify({"code": 500, "message": "HTML中未找到<head>标签"}), 500

                # Save back
                pc.delete_file(remote_path)
                pc.create_file(remote_path, is_dir=False)
                save_resp = pc.save_file(remote_path, html)
                if save_resp.get("code") == 200:
                    pc.reload_openresty()
                    return jsonify({"code": 200, "message": "Meta标签已注入静态站点并重载"})
                return jsonify({"code": 500, "message": f"保存失败: {save_resp.get('message', '')}"}), 500
            except Exception as e:
                logger.error(f"inject_meta static error site={site_id}: {e}")
                return jsonify({"code": 500, "message": str(e)[:200]}), 500

        # WordPress site: inject via admin session
        if not site.get("url") or not site.get("admin_name") or not site.get("admin_password"):
            return jsonify({"code": 400, "message": "站点缺少登录信息(URL/管理员/密码)"}), 400
        try:
            wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
            result = wp.inject_meta_tag(meta_tag)
            code = 200 if result.get("success") else 500
            return jsonify({"code": code, "message": result.get("message", ""), "data": result}), code
        except Exception as e:
            logger.error(f"inject_meta_tag error site={site_id}: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ------------------------------------------------------------------
    # Helper: resolve CloakBrowser profile path from site + optional input
    # ------------------------------------------------------------------
    def _resolve_site_profile(site: dict, profile_dir: str = "") -> str:
        """解析站点关联的 CloakBrowser profile 完整路径。

        优先级：
        1. profile_dir 已是存在的绝对路径 → 直接返回
        2. 指纹启用 + 站点有关联 profile → 用 resolve_profile_path 解析
        3. 指纹启用 + profile_dir 是裸名 → 用 resolve_profile_path 解析
        """
        if profile_dir and os.path.isabs(profile_dir) and os.path.isdir(profile_dir):
            return os.path.abspath(profile_dir)

        fingerprint_enabled = get_global_config().get("fingerprint_enabled", "false") == "true"
        profile_name = site.get("cloakbrowser_profile_name") or ""

        # Always prefer site's associated profile when fingerprint is enabled
        if fingerprint_enabled and profile_name:
            try:
                return resolve_profile_path(profile_name)
            except FileNotFoundError:
                pass  # fall through to try profile_dir

        # Resolve profile_dir as bare name
        if profile_dir:
            try:
                return resolve_profile_path(profile_dir)
            except FileNotFoundError:
                raise

        raise FileNotFoundError("请指定 CloakBrowser profile 目录")

    def _get_site_brand_kit(site: dict) -> dict | None:
        """Return the brand kit linked to a site via cloakbrowser_profile_name, if any."""
        profile_name = site.get("cloakbrowser_profile_name") or ""
        if not profile_name:
            return None
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM brand_kits WHERE cloakbrowser_profile_name = ? LIMIT 1",
                (profile_name,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _resolve_business_info(site: dict) -> dict:
        """从站点关联的品牌套件中提取 business_info，用于 GMC 注册自动填写。

        Returns dict with: company_name, address, city, state_code, postcode, country, etc.
        """
        bk = _get_site_brand_kit(site)
        if not bk:
            return {}
        bi = bk.get("business_info") or {}
        if isinstance(bi, str):
            try:
                bi = json.loads(bi)
            except (json.JSONDecodeError, TypeError):
                return {}
        return bi

    def _resolve_google_account_from_site(site: dict) -> tuple:
        """从站点关联的品牌套件中提取 Google 账户凭据。返回 (email, password, totp_secret)。"""
        try:
            bk = _get_site_brand_kit(site)
            if bk and bk.get("google_account_id"):
                ga = get_google_account(bk["google_account_id"])
                if ga:
                    return ga.get("email", ""), ga.get("password", ""), ga.get("totp_secret", "")
        except Exception:
            pass
        return "", "", ""

    @app.route("/api/sites/<int:site_id>/mc-status", methods=["GET"])
    @jwt_required()
    def get_mc_status(site_id):
        """Get MC registration and feed status for a site."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404
        return jsonify({"code": 200, "data": {
            "google_feed_url": site.get("google_feed_url", ""),
            "google_verification_method": site.get("google_verification_method", ""),
            "google_verification_done": bool(site.get("google_verification_done", 0)),
            "google_mc_account_id": site.get("google_mc_account_id", ""),
        }})

    def _get_design_progress_message(site_id):
        """Get the current design progress message from bg_task."""
        try:
            row = get_db().execute(
                "SELECT message FROM bg_tasks WHERE site_id = ? AND task_type = 'deploy_static' ORDER BY id DESC LIMIT 1",
                (site_id,),
            ).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
        return ""

    @app.route("/api/sites/<int:site_id>/pipeline-status", methods=["GET"])
    @jwt_required()
    def get_pipeline_status(site_id):
        """Get timeline pipeline status for a site.

        Static sites:
          ① dns_resolved → ② site_created → ③ design → ④ files_uploaded
        WordPress sites (legacy):
          ① wp_deployed → ② demo_imported → ③ brand_configured → ④ gmc_registered
        """
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        is_static = site.get("site_type") == "static"
        is_active = site.get("status") == "active"
        stitch_status = site.get("stitch_design_status", "")

        # Build per-screen progress for static sites
        STITCH_PAGE_NAMES = {"home":"首页","product":"产品页","cart":"购物车","checkout":"结账页",
                             "order":"订单确认","about":"关于我们","contact":"联系我们","faq":"FAQ",
                             "privacy":"隐私政策","terms":"服务条款","shipping":"配送信息","returns":"退换政策"}
        stitch_screen_progress = []
        if is_static and stitch_status in ("starting", "generating", "complete"):
            brand_kit_id = site.get("brand_kit_id")
            completed_screens = {}
            if brand_kit_id:
                try:
                    bk = get_brand_kit(brand_kit_id)
                    if bk:
                        raw = bk.get("stitch_screens", "")
                        completed_screens = json.loads(raw) if raw and raw.strip() else {}
                except Exception:
                    pass

            # Read current bg_task message to detect which screen is generating
            generating_page = ""
            try:
                row = get_db().execute(
                    "SELECT message FROM bg_tasks WHERE site_id = ? AND task_type = 'deploy_static' ORDER BY id DESC LIMIT 1",
                    (site_id,),
                ).fetchone()
                if row and row[0]:
                    msg = row[0]
                    for pt, cn in STITCH_PAGE_NAMES.items():
                        if cn in msg and ("正在生成" in msg or "复用已有" in msg):
                            generating_page = pt
                            break
            except Exception:
                pass

            for pt, cn in STITCH_PAGE_NAMES.items():
                if pt in completed_screens:
                    stitch_screen_progress.append({"page": pt, "name": cn, "status": "complete"})
                elif pt == generating_page:
                    stitch_screen_progress.append({"page": pt, "name": cn, "status": "generating"})
                else:
                    stitch_screen_progress.append({"page": pt, "name": cn, "status": "pending"})

        data = {
            "site_type": site.get("site_type", "wordpress"),
            # Static site stages
            "dns_resolved": bool(site.get("cf_dns_record_id")),
            "site_created": bool(site.get("panel_website_id") or site.get("static_dir")),
            # Design stage: for static sites, shows Stitch progress
            "design_started": stitch_status in ("starting", "generating", "complete"),
            "design_generating": stitch_status in ("starting", "generating"),
            "design_complete": stitch_status == "complete",
            "design_label": "Stitch" if stitch_status == "complete" else ("生成中" if stitch_status in ("starting", "generating") else ""),
            "files_uploaded": is_active,
            "design_message": _get_design_progress_message(site_id),
            "stitch_screen_progress": stitch_screen_progress,
            # WordPress legacy stages
            "wp_deployed": bool(site.get("panel_website_id")) if not is_static else False,
            "demo_imported": bool(site.get("demo_imported", 0)) if not is_static else False,
            "demo_name": site.get("demo_name", ""),
            "brand_configured": bool(site.get("brand_kit_id") or site.get("brand_configured", 0)),
            "gmc_registered": bool(site.get("google_mc_account_id", "") or site.get("google_feed_url", "")),
        }
        return jsonify({"code": 200, "data": data})

    # ------------------------------------------------------------------

    def _auto_diagnose_on_failure(task_id, success):
        """任务失败时自动触发 AI 诊断，并保存诊断结果。"""
        if success:
            return
        try:
            import threading as _th
            def _bg_diag():
                try:
                    from services.gmc_diagnosis import (
                        diagnose_task, get_task_logs_as_list, get_task_type,
                    )
                    from task_logs import save_diagnosis, add_log
                    task_type = get_task_type(task_id)
                    log_entries = get_task_logs_as_list(task_id)
                    if log_entries:
                        report = diagnose_task(task_id, task_type, log_entries)
                        save_diagnosis(task_id, report.to_dict())
                        add_log(task_id, "info", "🤖 AI 自动诊断完成: " + report.root_cause, "diagnosis")
                        sol = report.solution[:200] if report.solution else ""
                        add_log(task_id, "info", "💡 建议方案: " + sol, "diagnosis")
                        logger.info("Auto-diagnosis complete for failed task %s", task_id)
                except Exception as diag_err:
                    logger.error("Auto-diagnosis error for task %s: %s", task_id, diag_err)
            _th.Thread(target=_bg_diag, daemon=True).start()
        except Exception as e:
            logger.error("Failed to start auto-diagnosis: %s", e)

    # GMC 自动化后台任务（带实时日志）
    # ------------------------------------------------------------------

    @app.route("/api/tasks/auto-verify-google-site", methods=["POST"])
    @jwt_required()
    def task_auto_verify_google_site():
        """Start auto-verification in background, return task_id for log polling."""
        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        profile_dir = (data.get("profile_dir") or "").strip()
        site = get_site(site_id) if site_id else None
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        try:
            profile_dir = _resolve_site_profile(site, profile_dir)
        except FileNotFoundError as e:
            return jsonify({"code": 400, "message": str(e)}), 400

        site_domain = site.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
        task_id = create_task("auto-verify", site_id)
        logger.info("Task %s: auto-verify started for site=%s domain=%s profile=%s",
                    task_id, site_id, site_domain, os.path.basename(profile_dir))

        import asyncio
        from services.mc_auto_register import auto_verify_google_site as do_auto_verify

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                google_email, google_password, google_totp_secret = _resolve_google_account_from_site(site)

                result = loop.run_until_complete(do_auto_verify(
                    profile_dir=profile_dir,
                    site_domain=site_domain,
                    wp_url=site.get("url", ""),
                    wp_username=site.get("admin_name", ""),
                    wp_password=site.get("admin_password", ""),
                    google_email=google_email,
                    google_password=google_password,
                    google_totp_secret=google_totp_secret,
                    log_callback=lambda level, msg, step: add_log(task_id, level, msg, step),
                ))
                complete_task(task_id, result.get("success", False), result)
                if result.get("success"):
                    from models import update_site
                    update_site(site_id, {
                        "google_verification_done": 1,
                        "google_verification_method": "meta",
                    })
            except Exception as e:
                add_log(task_id, "error", f"任务异常: {e}", "")
                complete_task(task_id, False, {"success": False, "message": str(e)})
                _auto_diagnose_on_failure(task_id, False)
            finally:
                loop.close()

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"code": 200, "data": {"task_id": task_id}, "message": "任务已启动"})

    @app.route("/api/tasks/generate-feed", methods=["POST"])
    @jwt_required()
    def task_generate_feed():
        """Start Google Shopping feed generation in background, return task_id for log polling."""
        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        site = get_site(site_id) if site_id else None
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        task_id = create_task("generate-feed", site_id)
        logger.info("Task %s: generate-feed started for site=%s url=%s", task_id, site_id, site.get("url", ""))

        def _run():
            try:
                from services.wordpress_client import WordPressAdminSession
                add_log(task_id, "info", "正在生成 Google Shopping Feed...", "feed")
                wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
                result = wp.generate_google_feed()
                if result.get("success"):
                    add_log(task_id, "info", f"Feed 生成成功: {result.get('products', 0)} 个产品", "feed")
                    if result.get("feed_url"):
                        add_log(task_id, "info", f"Feed URL: {result['feed_url']}", "feed")
                        from models import update_site
                        update_site(site_id, {"google_feed_url": result["feed_url"]})
                    complete_task(task_id, True, result)
                else:
                    add_log(task_id, "error", result.get("message", "Feed 生成失败"), "feed")
                    complete_task(task_id, False, result)
                    _auto_diagnose_on_failure(task_id, False)
            except Exception as e:
                add_log(task_id, "error", f"任务异常: {e}", "feed")
                complete_task(task_id, False, {"success": False, "message": str(e)})

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"code": 200, "data": {"task_id": task_id}, "message": "任务已启动"})

    @app.route("/api/tasks/register-mc", methods=["POST"])
    @jwt_required()
    def task_register_mc():
        """Start MC registration in background, return task_id for log polling."""
        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        profile_dir = (data.get("profile_dir") or "").strip()
        site = get_site(site_id) if site_id else None
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        try:
            profile_dir = _resolve_site_profile(site, profile_dir)
        except FileNotFoundError as e:
            return jsonify({"code": 400, "message": str(e)}), 400

        cfg = get_global_config()
        country = cfg.get("google_default_country", "US")
        timezone = cfg.get("google_default_timezone", "America/Chicago")
        site_domain = site.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
        feed_url = site.get("google_feed_url", "") or f"https://{site_domain}/wp-content/uploads/google-feed.xml"

        task_id = create_task("register-mc", site_id)
        logger.info("Task %s: register-mc started for site=%s domain=%s profile=%s",
                    task_id, site_id, site_domain, os.path.basename(profile_dir))

        import asyncio
        from services.mc_auto_register import register_gmc

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                google_email, google_password, google_totp_secret = _resolve_google_account_from_site(site)
                business_info = _resolve_business_info(site)

                result = loop.run_until_complete(register_gmc(
                    profile_dir=profile_dir,
                    site_url=site.get("url", site_domain),
                    google_email=google_email,
                    google_password=google_password,
                    google_totp_secret=google_totp_secret or "",
                    business_info=business_info or {},
                    feed_url=feed_url,
                    log_callback=lambda level, msg, step: add_log(task_id, level, msg, step),
                ))
                complete_task(task_id, result.get("success", False), result)
                if result.get("success") and result.get("mc_account_id"):
                    from models import update_site
                    update_site(site_id, {"google_mc_account_id": result.get("mc_account_id", "")})
                # Auto-inject meta tag if extracted during registration
                meta_tag = (result.get("meta_tag") or "").strip()
                if meta_tag and meta_tag.startswith("<meta"):
                    try:
                        add_log(task_id, "info", f"自动注入验证标签: {meta_tag[:100]}", "meta_inject")
                        from models import get_db as _get_db, update_global_config as _ugc
                        _ = _get_db()  # ensure the inject-meta logic can use the db
                        # Call the inject logic inline
                        site_dir = site.get("static_dir", "")
                        env = get_user_panel_environment(site.get("created_by") or 1)
                        if env:
                            pc = OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"])
                            nginx_alias = site.get("nginx_alias", "")
                            if site_dir.startswith("/www/"):
                                remote_path = f"/opt/1panel/apps/openresty/openresty{site_dir}/index.html"
                            elif site_dir.startswith("/opt/"):
                                remote_path = f"{site_dir}/index.html"
                            else:
                                remote_path = f"/opt/1panel/apps/openresty/openresty/www/sites/{nginx_alias}/index/index.html"
                            content_resp = pc.read_file(remote_path)
                            html = content_resp.get("data", {}).get("content", "") if isinstance(content_resp.get("data"), dict) else str(content_resp.get("data", ""))
                            if not html:
                                html = str(content_resp.get("data", ""))
                            if html and "<head" in html.lower():
                                if "</head>" in html:
                                    html = html.replace("</head>", f"    {meta_tag}\n</head>")
                                elif "<head>" in html:
                                    html = html.replace("<head>", f"<head>\n    {meta_tag}")
                                pc.delete_file(remote_path)
                                pc.create_file(remote_path, is_dir=False)
                                save_resp = pc.save_file(remote_path, html)
                                if save_resp.get("code") == 200:
                                    pc.reload_openresty()
                                    update_site(site_id, {"google_verification_done": 1})
                                    add_log(task_id, "info", "验证标签已自动注入站点", "meta_inject")
                                else:
                                    add_log(task_id, "warning", f"标签注入保存失败: {save_resp.get('message', '')}", "meta_inject")
                            else:
                                add_log(task_id, "warning", f"无法读取站点HTML({remote_path})，请手动注入: {meta_tag}", "meta_inject")
                    except Exception as meta_e:
                        add_log(task_id, "warning", f"自动注入验证标签失败: {meta_e}，请手动注入: {meta_tag}", "meta_inject")
            except Exception as e:
                add_log(task_id, "error", f"任务异常: {e}", "")
                complete_task(task_id, False, {"success": False, "message": str(e)})
                _auto_diagnose_on_failure(task_id, False)
            finally:
                loop.close()

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"code": 200, "data": {"task_id": task_id}, "message": "任务已启动"})

    @app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
    @jwt_required()
    def task_cancel(task_id):
        """Cancel a running task."""
        from models import get_db
        db = get_db()
        try:
            db.execute(
                "UPDATE bg_tasks SET status = 'failed', result = ? WHERE id = ? AND status = 'running'",
                ('{"success": false, "message": "用户取消"}', task_id),
            )
            db.commit()
            return jsonify({"code": 200, "message": "任务已取消"})
        finally:
            db.close()

    @app.route("/api/tasks/<task_id>/logs", methods=["GET"])
    @jwt_required()
    def task_logs(task_id):
        """Poll for new log entries and task status.

        Query params:
            after (int): only return logs with index >= after (default 0)

        Returns:
            { code, data: { logs: [...], status: "running"|"success"|"failed", result: {...} } }
        """
        after = request.args.get("after", 0, type=int)
        logs, status, result = get_task_logs(task_id, after)
        diagnosis = get_diagnosis(task_id) if status in ("success", "failed") else None
        return jsonify({
            "code": 200,
            "data": {
                "logs": logs,
                "status": status,
                "result": result,
                "diagnosis": diagnosis,
            }
        })

    
    # ------------------------------------------------------------------
    # AI 诊断 — 分析任务日志，自动生成解决方案
    # ------------------------------------------------------------------

    @app.route("/api/tasks/<task_id>/diagnose", methods=["POST"])
    @jwt_required()
    def task_diagnose(task_id):
        """触发 AI 诊断：分析 GMC 任务日志并返回解决方案。

        Returns:
            { code, data: { diagnosis: { root_cause, solution, severity, steps, errors } } }
        """
        from services.gmc_diagnosis import (
            diagnose_task, get_task_logs_as_list, get_task_type,
        )
        from task_logs import save_diagnosis, get_task_logs

        try:
            task_type = get_task_type(task_id)
            if task_type == "unknown":
                return jsonify({"code": 404, "message": "任务不存在"}), 404

            # 读取所有日志
            log_entries = get_task_logs_as_list(task_id)
            logs, status, result = get_task_logs(task_id, after=0)

            if not log_entries:
                return jsonify({
                    "code": 200,
                    "data": {
                        "diagnosis": {
                            "summary": "暂无日志数据，无法诊断。请等待任务开始后重试。",
                            "severity": "info",
                        }
                    }
                })

            # 执行 AI 诊断
            report = diagnose_task(task_id, task_type, log_entries)
            diagnosis_dict = report.to_dict()

            # 持久化诊断结果
            save_diagnosis(task_id, diagnosis_dict)

            logger.info(
                "Diagnosis complete for task %s: severity=%s root_cause=%s",
                task_id, report.severity, report.root_cause[:80],
            )

            return jsonify({
                "code": 200,
                "data": {
                    "diagnosis": diagnosis_dict,
                    "task_status": status,
                },
                "message": "诊断完成",
            })
        except Exception as e:
            logger.error("Diagnosis error for task %s: %s", task_id, e)
            return jsonify({"code": 500, "message": f"诊断异常: {str(e)[:200]}"}), 500


    @app.route("/api/tasks/<task_id>/diagnosis", methods=["GET"])
    @jwt_required()
    def get_task_diagnosis(task_id):
        """获取之前保存的诊断结果（如果有）。"""
        from task_logs import get_diagnosis
        try:
            diag = get_diagnosis(task_id)
            if diag:
                return jsonify({"code": 200, "data": {"diagnosis": diag}})
            return jsonify({"code": 200, "data": {"diagnosis": None}, "message": "尚无诊断结果"})
        except Exception as e:
            logger.error("get_diagnosis error: %s", e)
            return jsonify({"code": 500, "message": str(e)[:200]}), 500
    @app.route("/api/cloakbrowser/profiles/test", methods=["POST"])
    @jwt_required()
    def cloakbrowser_test_profile():
        """Test a CloakBrowser profile: launch browser, verify connectivity."""
        data = request.get_json(silent=True) or {}
        profile_name = (data.get("profile_name") or "").strip()
        if not profile_name:
            return jsonify({"code": 400, "message": "请指定 profile 名称"}), 400

        try:
            profile_dir = resolve_profile_path(profile_name)
        except FileNotFoundError as e:
            logger.error("TestProfile: %s", e)
            return jsonify({"code": 400, "message": str(e)}), 400

        logger.info("TestProfile: resolved '%s' → '%s'", profile_name, profile_dir)

        import asyncio, threading
        from services.mc_auto_register import load_profile_config, _normalize_proxy_for_launch, _build_launch_args

        async def _test_connectivity():
            config = load_profile_config(profile_dir) or {}
            proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
            launch_kwargs = {"headless": True, "user_data_dir": profile_dir, "timeout": 60000, "args": _build_launch_args(config)}
            if proxy:
                launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)

            from cloakbrowser import launch_persistent_context_async
            context = await launch_persistent_context_async(**launch_kwargs)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_navigation_timeout(60000)
            try:
                # Test 1: check exit IP via httpbin
                await page.goto("http://httpbin.org/ip", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(1)
                body = await page.inner_text("body")
                ip_info = body.strip() if body else "(unknown)"

                # Test 2: verify Google accessibility (critical for GMC)
                await page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(2)
                title = await page.title()
                body = await page.inner_text("body")
                body_lower = body.lower()

                if "unusual traffic" in body_lower or "captcha" in body_lower or "not a robot" in body_lower:
                    return {"success": False, "message": f"Google 检测到异常流量/验证码 (IP: {ip_info})", "ip": ip_info}
                if "google" not in title.lower() and "search" not in title.lower():
                    return {"success": False, "message": f"Google 页面异常: {title[:100]} (IP: {ip_info})", "ip": ip_info}

                return {"success": True, "message": f"Profile 可用 (Google 正常), 出口IP: {ip_info}", "ip": ip_info}
            finally:
                await context.close()

        result_holder = {}
        def _run_test():
            try:
                result_holder["result"] = asyncio.run(_test_connectivity())
            except Exception as ex:
                result_holder["error"] = str(ex)[:200]
        t = threading.Thread(target=_run_test, daemon=True)
        t.start()
        t.join(timeout=180)

        if "result" not in result_holder:
            err = result_holder.get("error", "测试超时(>3min)")
            logger.error("TestProfile: failed - %s", err)
            return jsonify({"code": 500, "message": err}), 500

        result = result_holder["result"]
        code = 200 if result.get("success") else 500
        logger.info("TestProfile: result success=%s message=%s",
                    result.get("success"), result.get("message", "")[:100])
        return jsonify({"code": code, "message": result.get("message", ""), "data": result}), code

    @app.route("/api/cloakbrowser/profiles", methods=["GET"])
    @jwt_required()
    def cloakbrowser_profiles():
        """List all CloakBrowser profiles with binding status from brand_kits."""
        try:
            from services.mc_auto_register import list_profiles
            data = list_profiles()
            # Enrich with brand kit binding info
            conn = get_db()
            try:
                bindings = conn.execute(
                    "SELECT id, name, cloakbrowser_profile_name FROM brand_kits "
                    "WHERE cloakbrowser_profile_name IS NOT NULL AND cloakbrowser_profile_name != ''"
                ).fetchall()
                bound_map = {}
                for b in bindings:
                    bound_map[b["cloakbrowser_profile_name"]] = {
                        "kit_id": b["id"], "kit_name": b["name"],
                    }
            finally:
                conn.close()
            for p in data:
                b = bound_map.get(p["name"])
                if b:
                    p["bound_kit_id"] = b["kit_id"]
                    p["bound_kit_name"] = b["kit_name"]
                    p["bound"] = True
                else:
                    p["bound"] = False
            return jsonify({"code": 200, "data": data})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/cloakbrowser/profiles", methods=["POST"])
    @jwt_required()
    def cloakbrowser_create_profile():
        """Create a new CloakBrowser profile with random fingerprint."""
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"code": 400, "message": "Profile 名称不能为空"}), 400
        # Sanitize name: only allow a-z, 0-9, -, _
        import re as _re
        name = _re.sub(r'[^a-zA-Z0-9\-_]', '-', name)
        try:
            from services.mc_auto_register import create_profile
            result = create_profile(
                name=name,
                google_email=(data.get("google_email") or "").strip(),
                proxy=(data.get("proxy") or "").strip(),
                country=(data.get("country") or "US").strip(),
                platform=(data.get("platform") or None),
            )
            # Auto-import proxy to pool (each profile = independent proxy entry)
            proxy_url = (data.get("proxy") or "").strip()
            if proxy_url:
                from models import get_db
                m = _re.match(r'socks5://([^:]+):([^@]+)@([^:]+):(\d+)', proxy_url)
                if m:
                    host = m.group(3)
                    port = int(m.group(4))
                    db = get_db()
                    db.execute(
                        "INSERT INTO proxies (proxy_url, proxy_type, ip, port) VALUES (?, 'socks5', ?, ?)",
                        (proxy_url, host, port),
                    )
                    db.commit()
            return jsonify({"code": 200, "message": "Profile 创建成功", "data": result})
        except FileExistsError:
            return jsonify({"code": 409, "message": f"Profile '{name}' 已存在"}), 409
        except Exception as e:
            logger.error(f"create_profile error: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/cloakbrowser/profiles/<name>", methods=["PUT"])
    @jwt_required()
    def cloakbrowser_update_profile(name):
        """Update a CloakBrowser profile config."""
        data = request.get_json(silent=True) or {}
        try:
            from services.mc_auto_register import update_profile
            result = update_profile(name, **{k: v for k, v in data.items() if v is not None})
            return jsonify({"code": 200, "message": "Profile 更新成功", "data": result})
        except FileNotFoundError:
            return jsonify({"code": 404, "message": f"Profile '{name}' 不存在"}), 404
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/cloakbrowser/profiles/<name>", methods=["DELETE"])
    @jwt_required()
    def cloakbrowser_delete_profile(name):
        """Delete a CloakBrowser profile (including cookies)."""
        try:
            from services.mc_auto_register import delete_profile
            ok = delete_profile(name)
            if ok:
                return jsonify({"code": 200, "message": f"Profile '{name}' 已删除"})
            return jsonify({"code": 404, "message": f"Profile '{name}' 不存在"}), 404
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- 1Panel Status ----

    @app.route("/api/panel/status", methods=["GET"])
    @jwt_required()
    def panel_status():
        try:
            resp = _get_panel_client().search_apps(name="wordpress")
            if resp.get("code") == 200:
                return jsonify({"code": 200, "data": {"connected": True}})
            return jsonify({"code": 200, "data": {"connected": False, "message": resp.get("message", "")}})
        except Exception as e:
            return jsonify({"code": 200, "data": {"connected": False, "message": str(e)}})

    @app.route("/api/panel/sync", methods=["POST"])
    @jwt_required()
    def panel_sync():
        """Sync local sites with 1Panel actual state.

        For each local site:
        - Verify the 1Panel website still exists; if not, clear panel IDs
        - Verify the 1Panel app install still exists; if not, clear panel IDs
        - If 1Panel has websites/apps not in our DB, optionally import them

        Also reconciles:
        - Orphaned WordPress apps (installed but no website in 1Panel)
        - Sites where the 1Panel website was deleted externally
        """
        results = {"updated": 0, "cleared": 0, "warnings": 0, "imported": 0, "errors": [], "sites": []}
        try:
            def _fetch_panel_data(pc):
                """Fetch all websites + apps from one 1Panel environment."""
                websites = {}
                apps = {}
                page = 1
                while True:
                    ws_resp = pc.search_websites(page=page, page_size=100)
                    if ws_resp.get("code") != 200:
                        break
                    items = (ws_resp.get("data") or {}).get("items") or []
                    if not items:
                        break
                    for w in items:
                        websites[w.get("id")] = w
                    page += 1
                page = 1
                while True:
                    app_resp = pc.search_installed_apps(name="", page=page, page_size=100)
                    if app_resp.get("code") != 200:
                        break
                    items = (app_resp.get("data") or {}).get("items") or []
                    if not items:
                        break
                    for a in items:
                        apps[a.get("id")] = a
                    page += 1
                return websites, apps

            # Group sites by panel environment
            sites = list_sites()
            env_sites = {}  # env_key -> {"pc": client, "sites": [...], "websites": {}, "apps": {}}
            default_pc = _get_panel_client()
            default_key = "default"

            for site in sites:
                env = None
                try:
                    env = get_user_panel_environment(site.get("created_by") or 1)
                except Exception:
                    pass
                if env:
                    env_key = f"{env['host']}:{env['port']}"
                else:
                    env_key = default_key

                if env_key not in env_sites:
                    pc = OnePanelClient(host=env["host"], port=env["port"], api_key=env["api_key"]) if env else default_pc
                    env_sites[env_key] = {"pc": pc, "sites": [], "websites": None, "apps": None}
                env_sites[env_key]["sites"].append(site)

            # Fetch panel data per environment and check sites
            for env_key, ed in env_sites.items():
                pc = ed["pc"]
                websites, apps = _fetch_panel_data(pc)
                ed["websites"] = websites
                ed["apps"] = apps
                logger.info("Sync: env %s: %d websites, %d apps for %d sites",
                            env_key, len(websites), len(apps), len(ed["sites"]))

                for site in ed["sites"]:
                    sid = site.get("id")
                    site_name = site.get("site_name", "")
                    pwid = site.get("panel_website_id")
                    paid = site.get("panel_app_install_id")
                    updates = {}
                    site_status = "ok"

                    if pwid:
                        if pwid not in websites:
                            logger.warning(f"Sync: site {sid} ({site_name}) panel_website_id={pwid} not found in env {env_key} — KEPT (may be wrong env, check operator binding)")
                            results["warnings"] = results.get("warnings", 0) + 1

                    if paid and paid not in apps:
                        logger.warning(f"Sync: site {sid} ({site_name}) panel_app_install_id={paid} not found in env {env_key} — KEPT")

                    if pwid and not paid and pwid in websites:
                        pw = websites[pwid]
                        pw_app_id = pw.get("appInstallId")
                        if pw_app_id and pw_app_id in apps:
                            updates["panel_app_install_id"] = pw_app_id
                            results["updated"] += 1
                            site_status = "updated"
                            logger.info(f"Sync: filled missing app_install_id={pw_app_id} for site {sid}")

                # Forward-link: site has no panel IDs → find matching 1Panel website
                current_pwid = updates.get("panel_website_id", pwid)
                current_paid = updates.get("panel_app_install_id", paid)
                if not current_pwid and not current_paid:
                    nginx_alias = site.get("nginx_alias", "")
                    # Build candidate aliases: 1Panel may use dots (cnusel.com) or dashes (cnusel-com)
                    candidate_aliases = []
                    for raw in [nginx_alias, site_name]:
                        if raw:
                            candidate_aliases.extend([raw, raw.replace(".", "-"), raw.replace("-", ".")])
                    seen_aliases = set()
                    candidate_aliases = [a for a in candidate_aliases if a and not (a in seen_aliases or seen_aliases.add(a))]

                    for pw in websites.values():
                        if pw.get("alias") in candidate_aliases or pw.get("primaryDomain") == site_name:
                            updates["panel_website_id"] = pw.get("id")
                            updates["panel_app_install_id"] = pw.get("appInstallId")
                            results["updated"] += 1
                            site_status = "updated"
                            logger.info(f"Sync: linked site {sid} ({site_name}) to 1Panel website {pw.get('id')} (alias={pw.get('alias')})")
                            break

                if updates:
                    try:
                        update_site_fields(sid, updates)
                    except Exception as e:
                        results["errors"].append(f"Site {sid}: {e}")
                        site_status = "error"

                results["sites"].append({
                    "id": sid,
                    "site_name": site_name,
                    "panel_website_id": updates.get("panel_website_id", pwid),
                    "panel_app_install_id": updates.get("panel_app_install_id", paid),
                    "status": site_status,
                })

            # Check for WordPress apps in 1Panel that have NO website (orphaned)
            all_websites = {}
            all_wp_apps = []
            for ed in env_sites.values():
                all_websites.update(ed.get("websites", {}))
                for a in ed.get("apps", {}).values():
                    if a.get("appKey") == "wordpress":
                        all_wp_apps.append(a)
            linked_app_ids = set(w.get("appInstallId") for w in all_websites.values() if w.get("appInstallId"))
            orphaned_wp_apps = [a for a in all_wp_apps if a.get("id") not in linked_app_ids]
            results["orphaned_wp_apps"] = len(orphaned_wp_apps)
            results["orphaned_wp_app_details"] = [
                {"id": a.get("id"), "name": a.get("name"), "status": a.get("status"), "httpPort": a.get("httpPort")}
                for a in orphaned_wp_apps
            ]

            # Import orphaned WordPress apps as new sites (if requested)
            import_orphans = (request.get_json(silent=True) or {}).get("import_orphans", False)
            if import_orphans and orphaned_wp_apps:
                for app in orphaned_wp_apps:
                    # Check if already in our DB
                    existing = False
                    for site in sites:
                        if site.get("panel_app_install_id") == app.get("id"):
                            existing = True
                            break
                    if not existing:
                        try:
                            domain = app.get("name", "").replace("-", ".")
                            port = app.get("httpPort", 8081)
                            new_site = create_site({
                                "site_name": domain,
                                "url": f"http://{domain}",
                                "port": port,
                                "admin_name": "admin",
                                "admin_password": "",
                                "panel_app_install_id": app.get("id"),
                                "panel_website_id": None,
                                "nginx_alias": app.get("name"),
                                "tag": "imported",
                                "created_by": get_current_user_id(),
                            })
                            results["imported"] += 1
                            logger.info(f"Sync: imported orphaned WP app {app.get('id')} as site {new_site.get('id')}")
                        except Exception as e:
                            results["errors"].append(f"Import {app.get('name')}: {e}")

            # Detect 1Panel websites with WordPress apps that aren't tracked locally
            # These are the normal case: 1Panel one-click deploy creates a website + WordPress app together
            local_panel_website_ids = set(s.get("panel_website_id") for s in sites if s.get("panel_website_id"))
            local_panel_app_ids = set(s.get("panel_app_install_id") for s in sites if s.get("panel_app_install_id"))
            wp_app_ids = set(a.get("id") for a in all_wp_apps)
            untracked_wp_websites = [
                w for w in all_websites.values()
                if w.get("id") not in local_panel_website_ids
                and w.get("appInstallId") in wp_app_ids
                and w.get("appInstallId") not in local_panel_app_ids
            ]
            results["untracked_websites"] = len(untracked_wp_websites)
            results["untracked_website_details"] = [
                {"id": w.get("id"), "alias": w.get("alias"), "primaryDomain": w.get("primaryDomain"),
                 "appInstallId": w.get("appInstallId")}
                for w in untracked_wp_websites
            ]

            # Import untracked WordPress websites as new sites (if requested)
            if import_orphans and untracked_wp_websites:
                for w in untracked_wp_websites:
                    app_id = w.get("appInstallId")
                    app = panel_apps.get(app_id, {})
                    try:
                        domain = w.get("alias") or w.get("primaryDomain", "")
                        port = app.get("httpPort", 8081)
                        new_site = create_site({
                            "site_name": domain,
                            "url": f"http://{domain}",
                            "port": port,
                            "admin_name": "admin",
                            "admin_password": "",
                            "panel_app_install_id": app_id,
                            "panel_website_id": w.get("id"),
                            "nginx_alias": domain,
                            "tag": "imported",
                            "created_by": get_current_user_id(),
                        })
                        results["imported"] += 1
                        logger.info(f"Sync: imported untracked 1Panel website {w.get('id')} as site {new_site.get('id')}")
                    except Exception as e:
                        results["errors"].append(f"Import website {w.get('alias', 'unknown')}: {e}")

            # Detect orphaned databases (wp_* databases not linked to any local site)
            try:
                db_resp = _get_panel_client().search_databases(name="wp_")
                orphaned_dbs = []
                if db_resp.get("code") == 200:
                    db_items = (db_resp.get("data") or {}).get("items") or []
                    known_db_names = set(site.get("db_name") for site in sites if site.get("db_name"))
                    for d in db_items:
                        d_name = d.get("name", "")
                        if d_name.startswith("wp_") and d_name not in known_db_names:
                            orphaned_dbs.append({"id": d.get("id"), "name": d_name, "type": d.get("type", "")})
                # Report orphaned databases for informational purposes only.
                # NEVER auto-delete databases on 1Panel — that is destructive
                # and can delete databases of sites we just imported.
                results["orphaned_databases"] = len(orphaned_dbs)
                results["orphaned_db_details"] = orphaned_dbs
            except Exception as de:
                logger.warning(f"Sync: orphaned DB detection failed: {de}")

            return jsonify({"code": 200, "data": results})
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return jsonify({"code": 500, "message": f"同步失败: {str(e)[:100]}"}), 500

    @app.route("/api/panel/test-wp-install", methods=["POST"])
    @jwt_required()
    def test_wp_install():
        """Test WordPress auto-install on a specific port."""
        data = request.get_json(silent=True) or {}
        port = data.get("port")
        domain = data.get("domain", "test.local")
        if not port:
            return jsonify({"code": 400, "message": "port required"}), 400
        result = auto_install_wordpress(
            container_name="",
            site_url=f"http://{domain}",
            site_title=domain,
            admin_user="admin",
            admin_password="Test123456!",
            admin_email=f"admin@{domain}",
            port=port,
        )
        return jsonify({"code": 200, "data": result})

    @app.route("/api/wordpress/install-status/<int:site_id>", methods=["GET"])
    @jwt_required()
    def wp_install_status(site_id):
        """Check WordPress installation status for a site."""
        task = get_bg_task_by_site(site_id)
        if task:
            return jsonify({"code": 200, "data": {
                "site_id": site_id,
                "status": task.get("status", "unknown"),
                "message": task.get("message", ""),
            }})
        return jsonify({"code": 200, "data": {
            "site_id": site_id,
            "status": "unknown",
            "message": "未找到安装任务",
        }})

    @app.route("/api/sites/<int:site_id>/install-theme", methods=["POST"])
    @jwt_required()
    def install_theme_to_site(site_id):
        """Install and activate themes on a WordPress site."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            site_url = site["url"].rstrip("/")
            admin_user = site.get("admin_name", "admin")
            admin_password = site.get("admin_password", "")

            # Refresh PHP limits before heavy operations (theme install + demo import)
            _fix_wp_timeout(site_url, admin_user, admin_password)

            theme_files = _scan_theme_zips()
            if not theme_files:
                return jsonify({"code": 400, "message": "backend/themes/ 目录下没有找到主题zip文件"}), 400

            results = install_themes_to_site(site_url, admin_user, admin_password, theme_files)
            # Apply admin zh_CN locale after theme install
            _theme_slug = theme_files[0][0] if theme_files else "woodmart"
            _apply_post_install_configs(site_url, admin_user, admin_password,
                                         theme_slug=_theme_slug)
            return jsonify({"code": 200, "data": {"results": results}})
        except Exception as e:
            logger.error(f"Failed to install theme: {e}")
            return jsonify({"code": 500, "message": f"安装主题失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/install-plugins", methods=["POST"])
    @jwt_required()
    def install_plugins_to_site_endpoint(site_id):
        """Install and activate plugins on an existing WordPress site."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            plugin_ids = data.get("plugin_ids", [])
            if not plugin_ids:
                return jsonify({"code": 400, "message": "未选择插件"}), 400

            site_url = site["url"].rstrip("/")
            admin_user = site.get("admin_name", "admin")
            admin_password = site.get("admin_password", "")

            results = install_plugins_to_site(site_url, admin_user, admin_password, plugin_ids)
            return jsonify({"code": 200, "data": {"results": results}})
        except Exception as e:
            logger.error(f"Failed to install plugins: {e}")
            return jsonify({"code": 500, "message": f"安装插件失败: {str(e)[:100]}"}), 500

    # ---- Demo Import (WoodMart) ----
    import_demo_status = {}

    @app.route("/api/sites/<int:site_id>/prebuilt-demos", methods=["GET"])
    @jwt_required()
    def list_prebuilt_demos(site_id):
        """List available WoodMart theme demo imports."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败"}), 400
            demos = wp.list_prebuilt_demos()
            return jsonify({"code": 200, "data": demos})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/prebuilt-demos/import", methods=["POST"])
    @jwt_required()
    def import_prebuilt_demo(site_id):
        """Trigger a WoodMart theme demo import (runs in background)."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            data = request.get_json(silent=True) or {}
            demo_id = data.get("demo_id", "")
            if not demo_id:
                return jsonify({"code": 400, "message": "请选择演示"}), 400

            site_url = f"http://{site['site_name']}"
            _fix_wp_timeout(site_url, site.get("admin_name", "admin"), site.get("admin_password", ""))

            import_demo_status[demo_id] = {"status": "running", "message": "Import started"}

            def _do_demo_import():
                with app.app_context():
                    try:
                        wp = WordPressAdminSession(
                            site_url=f"http://{site['site_name']}",
                            username=site.get("admin_name", "admin"),
                            password=site.get("admin_password", ""),
                        )
                        site_url = f"http://{site['site_name']}"
                        try:
                            _ck = http_requests.get(site_url, timeout=10, allow_redirects=True)
                            logger.info("Demo import: site check -> HTTP %s", _ck.status_code)
                        except Exception as _ce:
                            logger.warning("Demo import: site check failed: %s", _ce)

                        logged_in = False
                        for retry in range(5):
                            if wp.login():
                                logged_in = True
                                break
                            if retry < 4:
                                delay = 10 * (retry + 1)
                                logger.info("Demo import login retry %d/5 in %ds for %s", retry + 1, delay, site['site_name'])
                                time.sleep(delay)
                        if not logged_in:
                            import_demo_status[demo_id] = {"status": "failed", "message": "WordPress登录失败（已重试5次）"}
                            return
                        result = wp.import_prebuilt_demo(demo_id)
                        logger.info("Demo import result for %s: %s", demo_id, result)
                        if result["success"]:
                            import_demo_status[demo_id] = {
                                "status": "success",
                                "message": result.get("message", ""),
                            }
                            update_site(site_id, {"demo_imported": 1, "demo_name": demo_id})
                        else:
                            import_demo_status[demo_id] = {
                                "status": "failed",
                                "message": result.get("message", ""),
                            }
                    except Exception as e:
                        logger.error("Demo import exception for %s: %s", demo_id, e)
                        import_demo_status[demo_id] = {"status": "failed", "message": str(e)[:200]}

            threading.Thread(target=_do_demo_import, daemon=True).start()
            return jsonify({"code": 200, "message": "导入已开始，请稍候..."})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/prebuilt-demos/status", methods=["GET"])
    @jwt_required()
    def check_demo_import_status(site_id):
        """Poll for demo import status."""
        demo_id = request.args.get("demo_id", "")
        status = import_demo_status.pop(demo_id, None) if demo_id else {}
        if not status:
            return jsonify({"code": 200, "data": {"status": "not_found"}})
        return jsonify({"code": 200, "data": status})
    # ---- Open Browser (CloakBrowser via site profile) ----
    @app.route("/api/sites/<int:site_id>/open-browser", methods=["POST"])
    @jwt_required()
    def open_site_browser(site_id):
        """Launch CloakBrowser with the site's fingerprint profile, navigate to site URL."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            profile_name = (site.get("cloakbrowser_profile_name") or "").strip()
            if not profile_name:
                return jsonify({"code": 400, "message": "该站点未绑定指纹环境，请在品牌套件中关联 CloakBrowser Profile"}), 400

            from services.mc_auto_register import resolve_profile_path, load_profile_config, _build_launch_args, _unlock_profile, _normalize_proxy_for_launch

            try:
                profile_dir = resolve_profile_path(profile_name)
            except FileNotFoundError as e:
                return jsonify({"code": 400, "message": str(e)}), 400

            config = load_profile_config(profile_dir) or {}
            proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
            tz = config.get("timezone", "America/Chicago")
            locale = config.get("locale", "en-US")
            fingerprint_args = _build_launch_args(config)
            domain = (site.get("url") or site.get("site_name", "")).rstrip("/")
            if not domain.startswith("http"):
                domain = f"https://{domain.lstrip('*.').lstrip('.')}"
            site_url = domain

            def _launch():
                import asyncio
                from cloakbrowser import launch_persistent_context_async
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    launch_kwargs = {
                        "headless": False,
                        "timezone": tz,
                        "locale": locale,
                        "args": fingerprint_args,
                        "humanize": True,
                        "stealth_args": False,
                    }
                    if proxy:
                        launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)
                        launch_kwargs["geoip"] = True
                    _unlock_profile(profile_dir)
                    browser_ctx = loop.run_until_complete(
                        launch_persistent_context_async(profile_dir, **launch_kwargs))
                    if browser_ctx.pages:
                        page = browser_ctx.pages[0]
                    else:
                        page = loop.run_until_complete(browser_ctx.new_page())
                    page.set_default_timeout(60000)
                    loop.run_until_complete(page.goto(site_url, wait_until="domcontentloaded"))
                    logger.info("OpenBrowser: launched profile=%s url=%s", profile_name, site_url)
                    # Keep browser open indefinitely (user closes manually)
                    while True:
                        import time
                        time.sleep(60)
                        # Check if browser is still alive
                        try:
                            loop.run_until_complete(page.title())
                        except Exception:
                            logger.info("OpenBrowser: browser closed for profile=%s", profile_name)
                            break
                except Exception as e:
                    logger.error("OpenBrowser: launch error for %s: %s", profile_name, e)
                finally:
                    try:
                        loop.run_until_complete(browser_ctx.close())
                    except Exception:
                        pass
                    loop.close()

            threading.Thread(target=_launch, daemon=True).start()
            vnc_url = "http://163.123.236.110:6080/vnc.html?autoconnect=true&resize=scale"
            return jsonify({
                "code": 200,
                "message": f"浏览器已启动，正在打开 {site_url}",
                "data": {"vnc_url": vnc_url, "site_url": site_url}
            })
        except Exception as e:
            logger.error("OpenBrowser: %s", e)
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Cloudflare ----
    def _get_cf_client(account_id=None):
        """Create a CloudflareClient from stored credentials (supports multi-account)."""
        from cloudflare_client import CloudflareClient
        token = _get_cf_token(account_id)
        return CloudflareClient(api_token=token)

    def _has_cf_credentials():
        """Check if any Cloudflare accounts are configured."""
        accts = list_cf_accounts(hide_secrets=True)
        return bool(accts)

    # ---- Cloudflare Account Management ----

    @app.route("/api/cloudflare/accounts", methods=["GET"])
    @jwt_required()
    def cf_list_accounts():
        """List all saved Cloudflare accounts (credentials masked)."""
        try:
            accounts = list_cf_accounts(hide_secrets=True)
            return jsonify({"code": 200, "data": accounts})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts", methods=["POST"])
    @jwt_required()
    def cf_create_account():
        """Add a new Cloudflare account after verifying credentials."""
        try:
            data = request.get_json(silent=True) or {}
            api_token = (data.get("api_token") or "").strip()
            name = (data.get("name") or "").strip()

            if not api_token:
                return jsonify({"code": 400, "message": "请提供API Token"}), 400

            from cloudflare_client import CloudflareClient
            cf = CloudflareClient(api_token=api_token)
            resp = cf.verify_token()

            if not resp.get("success"):
                errors = resp.get("errors", [])
                err_msg = str(errors)
                if any("6003" in str(e.get("code", "")) or "6111" in str(e.get("code", "")) for e in errors):
                    err_msg = "认证格式无效"
                elif any("1000" in str(e.get("code", "")) for e in errors):
                    err_msg = "API Token无效"
                return jsonify({"code": 400, "message": f"验证失败: {err_msg}"}), 400

            if not name:
                name = _next_cf_account_name()

            acct = create_cf_account({
                "name": name,
                "api_token": api_token,
                "auth_type": "token",
                "notes": (data.get("notes") or "").strip(),
            })

            return jsonify({"code": 200, "data": acct, "message": "账号已保存"})
        except Exception as e:
            logger.error(f"CF create account failed: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts/<int:account_id>", methods=["PUT"])
    @jwt_required()
    def cf_update_account(account_id):
        """Update a Cloudflare account (notes, name)."""
        try:
            acct = get_cf_account(account_id)
            if not acct:
                return jsonify({"code": 404, "message": "账号不存在"}), 404
            data = request.get_json(silent=True) or {}
            updates = {}
            for key in ("name", "notes"):
                if key in data:
                    updates[key] = data[key]
            if updates:
                from models import get_db
                db = get_db()
                sets = ", ".join(f"{k} = ?" for k in updates)
                vals = list(updates.values()) + [account_id]
                db.execute(f"UPDATE cloudflare_accounts SET {sets} WHERE id = ?", vals)
                db.commit()
            return jsonify({"code": 200, "message": "已更新"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts/<int:account_id>", methods=["DELETE"])
    @jwt_required()
    def cf_delete_account(account_id):
        """Delete a Cloudflare account."""
        try:
            delete_cf_account(account_id)
            return jsonify({"code": 200, "message": "账号已删除"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/accounts/<int:account_id>/default", methods=["PUT"])
    @jwt_required()
    def cf_set_default_account(account_id):
        """Set a Cloudflare account as the default."""
        try:
            set_default_cf_account(account_id)
            return jsonify({"code": 200, "message": "已设为默认账号"})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/verify", methods=["POST"])
    @jwt_required()
    def cf_verify_token():
        """Verify Cloudflare credentials and save as account."""
        import sys
        try:
            data = request.get_json(silent=True) or {}
            api_token = (data.get("api_token") or "").strip()
            sys.stderr.write(f"[CF-VERIFY] token len={len(api_token)} prefix={api_token[:12] if api_token else 'EMPTY'}\n")
            sys.stderr.flush()

            if not api_token:
                return jsonify({"code": 400, "message": "请提供API Token"}), 400

            from cloudflare_client import CloudflareClient
            cf = CloudflareClient(api_token=api_token)
            resp = cf.verify_token()
            sys.stderr.write(f"[CF-VERIFY] cf resp: success={resp.get('success')} errors={resp.get('errors')}\n")
            sys.stderr.flush()

            if resp.get("success"):
                name = data.get("name", "").strip()
                if not name:
                    name = _next_cf_account_name()
                acct = create_cf_account({
                    "name": name,
                    "api_token": api_token,
                    "auth_type": "token",
                    "notes": (data.get("notes") or "").strip(),
                })
                sys.stderr.write(f"[CF-VERIFY] saved: id={acct.get('id')} name={acct.get('name')}\n")
                sys.stderr.flush()
                return jsonify({"code": 200, "data": resp.get("result", {})})

            # Parse error for user-friendly message
            errors = resp.get("errors", [])
            err_msg = str(errors)
            if any("6003" in str(e.get("code", "")) or "6111" in str(e.get("code", "")) for e in errors):
                err_msg = "API Token格式无效，请检查格式是否正确"
            elif any("1000" in str(e.get("code", "")) for e in errors):
                err_msg = "API Token无效，请确认Token是否正确且未过期"
            sys.stderr.write(f"[CF-VERIFY] FAIL: {err_msg}\n")
            sys.stderr.flush()
            return jsonify({"code": 400, "message": f"验证失败: {err_msg}"}), 400
        except Exception as e:
            import traceback
            traceback.print_exc()
            sys.stderr.write(f"[CF-VERIFY] EXCEPTION: {e}\n")
            sys.stderr.flush()
            return jsonify({"code": 500, "message": f"验证失败: {str(e)[:100]}"}), 500

    @app.route("/api/cloudflare/zones", methods=["GET"])
    @jwt_required()
    def cf_list_zones():
        """List Cloudflare zones. Query: ?account_id=<id> to use specific account."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            resp = cf.list_zones()
            if resp.get("success"):
                return jsonify({"code": 200, "data": resp.get("result", [])})
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/dns-records/<zone_id>", methods=["GET"])
    @jwt_required()
    def cf_list_dns(zone_id):
        """List DNS records for a zone. Query: ?account_id=<id>&page=1&per_page=10."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            page = request.args.get("page", 1, type=int)
            per_page = request.args.get("per_page", 10, type=int)
            cf = _get_cf_client(account_id)
            resp = cf.list_dns_records(zone_id, page=page, per_page=per_page)
            if resp.get("success"):
                info = resp.get("result_info", {})
                return jsonify({
                    "code": 200,
                    "data": resp.get("result", []),
                    "total": info.get("total_count", 0),
                    "page": info.get("page", page),
                    "per_page": info.get("per_page", per_page),
                    "total_pages": info.get("total_pages", 1),
                })
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/dns-records/<zone_id>", methods=["POST"])
    @jwt_required()
    def cf_create_dns_record(zone_id):
        """Create a DNS record directly on Cloudflare. Body: type, name, content, ttl, proxied, account_id."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            data = request.get_json(silent=True) or {}
            record_type = data.get("type", "A")
            record_name = data.get("name", "").strip()
            record_content = data.get("content", "").strip()
            ttl = data.get("ttl", 1)
            proxied = data.get("proxied", False)
            if not record_name or not record_content:
                server_ip = _get_config_value("panel_server_ip") or config.PANEL_HOST
                record_content = record_content or server_ip
            if not record_name:
                return jsonify({"code": 400, "message": "请提供DNS记录名称"}), 400
            resp = cf.create_dns_record(zone_id, record_type, record_name, record_content, proxied=proxied, ttl=ttl)
            if resp.get("success"):
                return jsonify({"code": 200, "data": resp.get("result", {})})
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/dns-records/<zone_id>/<record_id>", methods=["PUT"])
    @jwt_required()
    def cf_update_dns(zone_id, record_id):
        """Update a DNS record. Body: type, name, content, ttl, proxied."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            data = request.get_json(silent=True) or {}
            resp = cf.update_dns_record(zone_id, record_id, data)
            if resp.get("success"):
                return jsonify({"code": 200, "data": resp.get("result", {})})
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/cloudflare/dns-records/<zone_id>/<record_id>", methods=["DELETE"])
    @jwt_required()
    def cf_delete_dns(zone_id, record_id):
        """Delete a DNS record."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            resp = cf.delete_dns_record(zone_id, record_id)
            if resp.get("success"):
                return jsonify({"code": 200, "data": resp.get("result", {})})
            return jsonify({"code": 500, "message": str(resp.get("errors", []))}), 500
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/sites/<int:site_id>/dns", methods=["POST"])
    @jwt_required()
    def cf_create_dns(site_id):
        """Create a DNS A record for a site via Cloudflare. Body: zone_id, proxied, server_ip, account_id."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            zone_id = data.get("zone_id")
            proxied = data.get("proxied", False)
            account_id = data.get("account_id")

            if not _has_cf_credentials():
                return jsonify({"code": 400, "message": "请先授权Cloudflare账户"}), 400

            server_ip = data.get("server_ip") or _get_config_value("panel_server_ip") or config.PANEL_HOST

            cf = _get_cf_client(account_id)

            domain = site["site_name"]
            if not zone_id:
                zone = cf.find_zone_by_name(domain)
                if not zone:
                    return jsonify({"code": 404, "message": f"未在Cloudflare中找到域名 {domain} 的区域，请确认域名已添加到Cloudflare"}), 404
                zone_id = zone["id"]

            resp = cf.create_dns_record(zone_id, "A", domain, server_ip, proxied=proxied)
            if resp.get("success"):
                record = resp.get("result", {})
                update_site_fields(site_id, {
                    "cf_zone_id": zone_id,
                    "cf_dns_record_id": record.get("id", ""),
                })
                return jsonify({"code": 200, "data": record})
            return jsonify({"code": 500, "message": "DNS记录创建失败: " + str(resp.get("errors", []))}), 500
        except Exception as e:
            logger.error(f"CF DNS create failed: {e}")
            return jsonify({"code": 500, "message": f"DNS创建失败: {str(e)[:100]}"}), 500

    @app.route("/api/cloudflare/status", methods=["GET"])
    @jwt_required()
    def cf_status():
        """Check Cloudflare connection status. Query: ?account_id=<id>."""
        try:
            if not _has_cf_credentials():
                return jsonify({"code": 200, "data": {"connected": False}})
            account_id = request.args.get("account_id", type=int)
            cf = _get_cf_client(account_id)
            resp = cf.verify_token()
            return jsonify({"code": 200, "data": {"connected": resp.get("success", False)}})
        except Exception:
            return jsonify({"code": 200, "data": {"connected": False}})
    # ---- Post-Install WordPress Configuration ----

    @app.route("/api/sites/<int:site_id>/wp-settings", methods=["GET"])
    @jwt_required()
    def get_wp_settings(site_id):
        """Get current WordPress settings for a site."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败，请检查管理员账号密码"}), 400

            settings = wp.get_settings()
            return jsonify({"code": 200, "data": settings})
        except Exception as e:
            logger.error(f"get_wp_settings: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/wp-settings", methods=["POST"])
    @jwt_required()
    def update_wp_settings(site_id):
        """Update WordPress settings for a site."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败"}), 400

            ok = wp.update_settings(data)
            return jsonify({"code": 200 if ok else 500, "message": "已保存" if ok else "保存失败"})
        except Exception as e:
            logger.error(f"update_wp_settings: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- AI-Powered WordPress Configuration ----
    @app.route("/api/sites/<int:site_id>/ai-config", methods=["POST"])
    @jwt_required()
    def start_ai_config(site_id):
        """Start AI-powered WordPress configuration using DeepSeek."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        data = request.get_json(silent=True) or {}
        brand_name = (data.get("brand_name") or "").strip()
        if not brand_name:
            return jsonify({"code": 400, "message": "品牌名称不能为空"}), 400

        from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
        deepseek_keys = get_deepseek_keys()
        if not deepseek_keys:
            return jsonify({"code": 400, "message": "请先在系统设置中配置 DeepSeek API Key"}), 400

        config_key = f"{site_id}_{uuid.uuid4().hex[:8]}"
        ai_config_status[config_key] = {
            "status": "running",
            "message": "AI 配置已启动",
            "steps": [
                {"label": "网站标题和副标题", "status": "pending", "message": ""},
                {"label": "站点图标 (SVG)",   "status": "pending", "message": ""},
                {"label": "允许注册设置",      "status": "pending", "message": ""},
                {"label": "默认用户角色",      "status": "pending", "message": ""},
                {"label": "时区设置",         "status": "pending", "message": ""},
            ],
        }

        def _do_ai_config():
            with app.app_context():
                try:
                    log_prefix = f"[AI配置 {config_key}]"
                    wp = WordPressAdminSession(
                        site_url=f"http://{site['site_name']}",
                        username=site.get("admin_name", "admin"),
                        password=site.get("admin_password", ""),
                    )
                    if not wp.login():
                        logger.error("%s WordPress 登录失败", log_prefix)
                        ai_config_status[config_key] = {"status": "failed", "message": "WordPress 登录失败", "steps": ai_config_status[config_key]["steps"]}
                        return

                    steps = ai_config_status[config_key]["steps"]

                    # --- Step 0: Call DeepSeek API ---
                    logger.info("%s 调用 DeepSeek API，品牌: %s", log_prefix, brand_name)
                    ai_config_status[config_key]["message"] = "正在调用 AI 生成配置..."

                    deepseek_prompt = f"""You are a WordPress site config expert. Generate English site title and tagline for a brand.

Brand name: {brand_name}

Respond with strict JSON only (no markdown code blocks):
{{
  "site_title": "Site title in English (concise, attractive, include brand name, 5 words max)",
  "tagline": "Tagline in English (describe the brand, 10 words max)",
  "svg_logo": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>...minimal modern SVG icon for this brand</svg>"
}}

Requirements:
1. site_title must reflect the brand name
2. tagline should be concise and appealing
3. svg_logo must be valid SVG, 512x512, minimal modern design, 2-3 colors, suitable as favicon
4. Return JSON only, no extra text."""

                    def _ds_call(key):
                        return http_requests.post(
                            "https://api.deepseek.com/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": "deepseek-chat",
                                "messages": [
                                    {"role": "system", "content": "你是一个专业的WordPress网站配置助手，只返回JSON格式数据。"},
                                    {"role": "user", "content": deepseek_prompt},
                                ],
                                "temperature": 0.8,
                                "max_tokens": 4096,
                            },
                            timeout=90,
                        )

                    try:
                        resp = rotate_deepseek(_ds_call, deepseek_keys)
                    except Exception as e:
                        logger.error("%s DeepSeek API 全部密钥失败: %s", log_prefix, e)
                        ai_config_status[config_key] = {"status": "failed", "message": f"DeepSeek API 全部密钥失败: {e}", "steps": steps}
                        return

                    if resp.status_code != 200:
                        logger.error("%s DeepSeek API 返回错误: HTTP %s %s", log_prefix, resp.status_code, resp.text[:500])
                        ai_config_status[config_key] = {"status": "failed", "message": f"DeepSeek API 返回错误: HTTP {resp.status_code}", "steps": steps}
                        return

                    result = resp.json()
                    ai_text = result["choices"][0]["message"]["content"].strip()

                    # Strip markdown code fences
                    if "```" in ai_text:
                        ai_text = re.sub(r'^```(?:json)?\s*\n?', '', ai_text)
                        ai_text = re.sub(r'\n?```\s*$', '', ai_text)

                    try:
                        ai_data = json.loads(ai_text)
                    except json.JSONDecodeError:
                        logger.warning("%s JSON 解析失败，尝试正则提取", log_prefix)
                        json_match = re.search(r'\{[\s\S]*\}', ai_text)
                        if json_match:
                            try:
                                ai_data = json.loads(json_match.group(0))
                            except json.JSONDecodeError:
                                logger.error("%s JSON 正则提取也失败: %s", log_prefix, ai_text[:500])
                                ai_config_status[config_key] = {"status": "failed", "message": "AI 返回格式无法解析", "steps": steps}
                                return
                        else:
                            logger.error("%s 响应中找不到 JSON: %s", log_prefix, ai_text[:500])
                            ai_config_status[config_key] = {"status": "failed", "message": "AI 返回格式无法解析", "steps": steps}
                            return

                    site_title = ai_data.get("site_title", brand_name)
                    tagline = ai_data.get("tagline", "")
                    svg_logo = ai_data.get("svg_logo", "")

                    logger.info("%s AI 生成: title='%s' tagline='%s' svg_len=%d",
                                log_prefix, site_title, tagline, len(svg_logo))

                    # --- Step 1: 网站标题和副标题 ---
                    logger.info("%s 步骤1: 设置网站标题和副标题", log_prefix)
                    steps[0]["status"] = "running"
                    steps[0]["message"] = f"设置: {site_title}"
                    ai_config_status[config_key]["message"] = "正在设置网站标题..."

                    try:
                        ok = wp.update_settings({"title": site_title, "description": tagline})
                        if ok:
                            steps[0]["status"] = "done"
                            steps[0]["message"] = f"标题: {site_title}"
                            logger.info("%s 步骤1 完成: title='%s' tagline='%s'", log_prefix, site_title, tagline)
                        else:
                            steps[0]["status"] = "failed"
                            steps[0]["message"] = "设置未生效"
                            logger.warning("%s 步骤1 失败: update_settings returned False", log_prefix)
                    except Exception as e:
                        steps[0]["status"] = "failed"
                        steps[0]["message"] = str(e)[:100]
                        logger.warning("%s 步骤1 失败: %s", log_prefix, e)

                    # --- Step 2: 站点图标 (SVG) ---
                    logger.info("%s 步骤2: 上传站点图标", log_prefix)
                    steps[1]["status"] = "running"
                    steps[1]["message"] = "正在生成并上传..."
                    ai_config_status[config_key]["message"] = "正在上传站点图标..."

                    if svg_logo and "<svg" in svg_logo.lower():
                        try:
                            icon_result = wp.upload_site_icon(svg_logo)
                            if icon_result.get("success"):
                                steps[1]["status"] = "done"
                                steps[1]["message"] = f"已上传 (ID: {icon_result.get('attachment_id')})"
                                logger.info("%s 步骤2 完成: attachment_id=%s", log_prefix, icon_result.get("attachment_id"))
                            else:
                                steps[1]["status"] = "failed"
                                steps[1]["message"] = icon_result.get("message", "上传失败")[:100]
                                logger.warning("%s 步骤2 失败: %s", log_prefix, icon_result.get("message"))
                        except Exception as e:
                            steps[1]["status"] = "failed"
                            steps[1]["message"] = str(e)[:100]
                            logger.warning("%s 步骤2 异常: %s", log_prefix, e)
                    else:
                        steps[1]["status"] = "done"
                        steps[1]["message"] = "AI 未生成有效 SVG，已跳过"
                        logger.warning("%s 步骤2 跳过: AI 未生成有效 SVG", log_prefix)

                    # --- Step 3: 允许注册 ---
                    logger.info("%s 步骤3: 启用用户注册", log_prefix)
                    steps[2]["status"] = "running"
                    steps[2]["message"] = "正在启用..."
                    ai_config_status[config_key]["message"] = "正在配置注册设置..."

                    try:
                        wp._post_options_page({"users_can_register": "1"})
                        steps[2]["status"] = "done"
                        steps[2]["message"] = "已启用"
                        logger.info("%s 步骤3 完成: 用户注册已启用", log_prefix)
                    except Exception as e:
                        steps[2]["status"] = "failed"
                        steps[2]["message"] = str(e)[:100]
                        logger.warning("%s 步骤3 失败: %s", log_prefix, e)

                    # --- Step 4: 默认用户角色 ---
                    logger.info("%s 步骤4: 设置默认角色为 customer", log_prefix)
                    steps[3]["status"] = "running"
                    steps[3]["message"] = "正在设置..."
                    ai_config_status[config_key]["message"] = "正在设置默认角色..."

                    try:
                        wp._post_options_page({"default_role": "customer"})
                        steps[3]["status"] = "done"
                        steps[3]["message"] = "已设置为 Customer"
                        logger.info("%s 步骤4 完成: default_role=customer", log_prefix)
                    except Exception as e:
                        steps[3]["status"] = "failed"
                        steps[3]["message"] = str(e)[:100]
                        logger.warning("%s 步骤4 失败: %s", log_prefix, e)

                    # --- Step 5: 时区 ---
                    logger.info("%s 步骤5: 设置时区为 America/Chicago", log_prefix)
                    steps[4]["status"] = "running"
                    steps[4]["message"] = "正在设置..."
                    ai_config_status[config_key]["message"] = "正在设置时区..."

                    try:
                        ok = wp.update_settings({"timezone": "America/Chicago"})
                        if ok:
                            steps[4]["status"] = "done"
                            steps[4]["message"] = "已设置为 America/Chicago"
                            logger.info("%s 步骤5 完成: timezone=America/Chicago", log_prefix)
                        else:
                            steps[4]["status"] = "failed"
                            steps[4]["message"] = "设置未生效"
                            logger.warning("%s 步骤5 失败: update_settings returned False", log_prefix)
                    except Exception as e:
                        steps[4]["status"] = "failed"
                        steps[4]["message"] = str(e)[:100]
                        logger.warning("%s 步骤5 失败: %s", log_prefix, e)

                    # --- Final ---
                    all_done = all(s["status"] == "done" for s in steps)
                    ai_config_status[config_key]["status"] = "success" if all_done else "failed"
                    ai_config_status[config_key]["message"] = "AI 配置完成" if all_done else "部分步骤失败，请查看详情"
                    logger.info("%s 全部完成: all_done=%s", log_prefix, all_done)
                    if not all_done:
                        for s in steps:
                            if s["status"] == "failed":
                                logger.warning("%s 失败步骤: %s → %s", log_prefix, s["label"], s["message"])

                except Exception as e:
                    logger.error("%s 未处理异常: %s\n%s", log_prefix, e, traceback.format_exc())
                    ai_config_status[config_key] = {
                        "status": "failed",
                        "message": str(e)[:200],
                        "steps": ai_config_status.get(config_key, {}).get("steps", []),
                    }

        threading.Thread(target=_do_ai_config, daemon=True).start()
        return jsonify({"code": 200, "data": {"config_key": config_key}, "message": "AI 配置已启动"})

    @app.route("/api/sites/<int:site_id>/ai-config/status", methods=["GET"])
    @jwt_required()
    def get_ai_config_status(site_id):
        """Poll AI config progress."""
        config_key = request.args.get("config_key", "")
        if not config_key:
            return jsonify({"code": 400, "message": "缺少 config_key"}), 400
        status = ai_config_status.get(config_key)
        if not status:
            return jsonify({"code": 200, "data": {"status": "not_found"}})
        return jsonify({"code": 200, "data": status})

    # ---- Simplified WooCommerce Configuration ----
    @app.route("/api/sites/<int:site_id>/woo-config", methods=["POST"])
    @jwt_required()
    def save_woo_config(site_id):
        """Save simplified WooCommerce store config + make site public."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        data = request.get_json(silent=True) or {}

        store_data = {}
        field_map = {
            "address": "woocommerce_store_address",
            "city": "woocommerce_store_city",
            "postcode": "woocommerce_store_postcode",
        }
        for src, dest in field_map.items():
            if data.get(src):
                store_data[dest] = data[src]

        if data.get("country_state"):
            store_data["woocommerce_default_country"] = data["country_state"]

        if data.get("allowed_countries"):
            store_data["woocommerce_allowed_countries"] = "specific"
            countries = [c.strip() for c in data["allowed_countries"].split(",") if c.strip()]
            store_data["woocommerce_specific_allowed_countries"] = countries
        else:
            store_data["woocommerce_allowed_countries"] = "all"

        # Make site public (disable coming soon mode)
        store_data["woocommerce_coming_soon"] = "no"

        wp = WordPressAdminSession(
            site_url=f"http://{site['site_name']}",
            username=site.get("admin_name", "admin"),
            password=site.get("admin_password", ""),
        )
        if not wp.login():
            return jsonify({"code": 400, "message": "WordPress 登录失败"}), 400

        logger.info("Saving WooCommerce config for site %s: %s", site_id, list(store_data.keys()))
        ok = wp.update_woocommerce_settings("general", store_data)
        if ok:
            logger.info("WooCommerce config saved: %s", store_data)
        else:
            logger.warning("WooCommerce config save returned False for site %s", site_id)
        return jsonify({"code": 200 if ok else 500, "message": "已保存" if ok else "保存失败"})

    # ---- Cloudflare SSL plugin (final step after brand kit) ----
    @app.route("/api/sites/<int:site_id>/install-cf-ssl", methods=["POST"])
    @jwt_required()
    def install_cf_ssl(site_id):
        """Install and activate Cloudflare Flexible SSL plugin from backend/plugins/.

        Called after brand kit application — SSL activation forces HTTPS,
        which invalidates any active HTTP session, so this must be last.
        """
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "Site not found"}), 404

        wp_url = f"http://{site['site_name']}"
        admin_user = site.get("admin_name", "admin")
        admin_password = site.get("admin_password", "")

        try:
            ssl_ok = _install_ssl_plugin(wp_url, admin_user, admin_password)
            logger.info("CF SSL plugin for site %s: success=%s", site_id, ssl_ok)
            if ssl_ok:
                update_site(site_id, {"brand_configured": 1})
                return jsonify({"code": 200, "message": "Cloudflare SSL 插件已安装并激活"})
            else:
                return jsonify({"code": 200, "message": "Cloudflare SSL 插件未找到，已跳过"})
        except Exception as e:
            logger.error("install_cf_ssl error for site %s: %s", site_id, e)
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Brand Kit Application (site creation step 5) ----
    brand_kit_apply_status = {}  # local worker cache; DB-backed for cross-worker

    def _get_bk_apply_status(config_key):
        """Read brand-kit apply status, falling back to global_config DB."""
        s = brand_kit_apply_status.get(config_key)
        if s is not None:
            return s
        # Cross-worker fallback: read from DB
        try:
            configs = get_global_config()
            raw = configs.get(f"bk_apply_{config_key}")
            if raw:
                s = json.loads(raw)
                brand_kit_apply_status[config_key] = s  # cache in local worker
                logger.info("bk-apply %s: loaded from DB fallback (cross-worker)", config_key)
                return s
        except Exception as e:
            logger.warning("bk-apply %s: DB fallback read error: %s", config_key, e)
        return None

    def _set_bk_apply_status(config_key, status_dict):
        """Write brand-kit apply status to both memory and DB."""
        brand_kit_apply_status[config_key] = status_dict
        try:
            update_global_config(f"bk_apply_{config_key}", status_dict)
        except Exception as e:
            logger.warning("Failed to persist bk_apply status to DB: %s", e)

    @app.route("/api/sites/<int:site_id>/apply-brand-kit", methods=["POST"])
    @jwt_required()
    def apply_brand_kit_route(site_id):
        """Apply a brand kit logo + footer settings to a WordPress site."""
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        data = request.get_json(silent=True) or {}
        brand_kit_id = data.get("brand_kit_id")
        footer_address = (data.get("footer_address") or "").strip()
        footer_phone = (data.get("footer_phone") or "").strip()
        footer_email = (data.get("footer_email") or "").strip()
        config_key = str(uuid.uuid4())[:8]

        logger.info("apply-brand-kit START: site=%s domain=%s brand_kit_id=%s addr=%s phone=%s email=%s config_key=%s",
                     site_id, site.get("site_name", "?"), brand_kit_id or "none",
                     footer_address or "(none)", footer_phone or "(none)", footer_email or "(none)", config_key)

        kit = None
        if brand_kit_id:
            kit = get_brand_kit(int(brand_kit_id))
            if not kit or kit.get("status") != "ready":
                logger.warning("apply-brand-kit %s: brand kit %s not ready (status=%s)", config_key, brand_kit_id,
                               kit.get("status") if kit else "not_found")
                return jsonify({"code": 400, "message": "品牌套件不可用（未就绪或不存在）"}), 400
            logger.info("apply-brand-kit %s: using kit id=%s name=%s dir=%s png_512=%s",
                        config_key, brand_kit_id, kit.get("name", "?"),
                        kit.get("directory", "?"), kit.get("png_512") or kit.get("png_256") or "?")

        _set_bk_apply_status(config_key, {
            "status": "running", "message": "准备中...",
            "steps": [
                {"label": "上传Logo图片", "status": "pending", "message": ""},
                {"label": "设置站点Logo和图标", "status": "pending", "message": ""},
                {"label": "保存页脚信息", "status": "pending", "message": ""},
            ],
        })

        def _do_apply():
            with app.app_context():
                try:
                    logger.info("apply-brand-kit %s: [THREAD] started", config_key)
                    current = _get_bk_apply_status(config_key)
                    if current is None:
                        logger.error("apply-brand-kit %s: [THREAD] status not found in DB!", config_key)
                        return
                    steps = current["steps"]
                    wp = WordPressAdminSession(
                        site_url=f"http://{site['site_name']}",
                        username=site.get("admin_name", "admin"),
                        password=site.get("admin_password", ""),
                    )
                    logger.info("apply-brand-kit %s: [THREAD] logging into %s", config_key, site["site_name"])
                    if not wp.login():
                        logger.error("apply-brand-kit %s: [THREAD] WP login FAILED for %s", config_key, site["site_name"])
                        _set_bk_apply_status(config_key, {"status": "failed", "message": "WordPress 登录失败", "steps": steps})
                        return
                    logger.info("apply-brand-kit %s: [THREAD] WP login OK", config_key)

                    attachment_id = None

                    if kit:
                        # Step 1: Upload logo image
                        logger.info("apply-brand-kit %s: [STEP1] uploading logo", config_key)
                        steps[0]["status"] = "running"
                        steps[0]["message"] = "正在上传Logo..."
                        kit_dir = kit.get("directory", "")
                        logo_file = kit.get("png_512") or kit.get("png_256") or ""
                        if kit_dir and logo_file:
                            logo_path = os.path.join(os.path.dirname(__file__), kit_dir, logo_file)
                            logger.info("apply-brand-kit %s: [STEP1] logo_path=%s exists=%s",
                                        config_key, logo_path, os.path.isfile(logo_path))
                            upload_result = wp.upload_image(logo_path)
                            logger.info("apply-brand-kit %s: [STEP1] upload_image result: %s", config_key, upload_result)
                            if upload_result.get("success"):
                                attachment_id = upload_result["attachment_id"]
                                steps[0]["status"] = "done"
                                steps[0]["message"] = f"Logo已上传 (ID: {attachment_id})"
                                _set_bk_apply_status(config_key, {"status": "running", "steps": steps, "message": "正在设置Logo..."})
                            else:
                                steps[0]["status"] = "failed"
                                steps[0]["message"] = upload_result.get("message", "上传失败")
                                _set_bk_apply_status(config_key, {"status": "failed", "message": "Logo上传失败", "steps": steps})
                                return
                        else:
                            logger.warning("apply-brand-kit %s: [STEP1] no logo file (dir=%s file=%s)", config_key, kit_dir, logo_file)
                            steps[0]["status"] = "done"
                            steps[0]["message"] = "无Logo文件"

                        # Step 2: Set site_logo + site_icon
                        if attachment_id:
                            logger.info("apply-brand-kit %s: [STEP2] setting site logo, attachment=%s", config_key, attachment_id)
                            steps[1]["status"] = "running"
                            steps[1]["message"] = "正在设置Logo..."
                            _set_bk_apply_status(config_key, {"status": "running", "steps": steps, "message": "正在设置Logo..."})
                            logo_result = wp.set_site_logo(attachment_id)
                            logger.info("apply-brand-kit %s: [STEP2] set_site_logo result: %s", config_key, logo_result)
                            if logo_result.get("success"):
                                steps[1]["status"] = "done"
                                steps[1]["message"] = "站点Logo和图标已设置"
                            else:
                                steps[1]["status"] = "failed"
                                steps[1]["message"] = logo_result.get("message", "设置失败")
                                _set_bk_apply_status(config_key, {"status": "failed", "message": logo_result.get("message", "Logo设置失败"), "steps": steps})
                                # Don't return — footer can still be saved
                        else:
                            steps[1]["status"] = "done"
                            steps[1]["message"] = "跳过（无Logo）"
                    else:
                        logger.info("apply-brand-kit %s: [STEP1+2] skipped (no brand kit)", config_key)
                        steps[0]["status"] = "done"
                        steps[0]["message"] = "跳过（未选择品牌套件）"
                        steps[1]["status"] = "done"
                        steps[1]["message"] = "跳过（无Logo）"

                    # Step 3: Save footer settings
                    if footer_address or footer_phone or footer_email:
                        logger.info("apply-brand-kit %s: [STEP3] saving footer (addr=%s phone=%s email=%s logo=%s)",
                                    config_key, footer_address or "?", footer_phone or "?", footer_email or "?", attachment_id)
                        steps[2]["status"] = "running"
                        steps[2]["message"] = "正在保存页脚信息..."
                        _set_bk_apply_status(config_key, {"status": "running", "steps": steps, "message": "正在保存页脚..."})
                        ok = wp.save_footer_settings(
                            address=footer_address, phone=footer_phone,
                            email=footer_email,
                            logo_attachment_id=attachment_id or 0,
                        )
                        logger.info("apply-brand-kit %s: [STEP3] save_footer_settings result: %s", config_key, ok)
                        steps[2]["status"] = "done" if ok else "failed"
                        steps[2]["message"] = "页脚信息已保存" if ok else "页脚信息保存失败"
                    else:
                        logger.info("apply-brand-kit %s: [STEP3] skipped (no footer info)", config_key)
                        steps[2]["status"] = "done"
                        steps[2]["message"] = "无页脚信息需保存"

                    logger.info("apply-brand-kit %s: [DONE] all steps complete, attachment_id=%s", config_key, attachment_id)
                    _set_bk_apply_status(config_key, {
                        "status": "success", "message": "品牌套件已应用",
                        "attachment_id": attachment_id, "steps": steps,
                    })
                except Exception as e:
                    logger.error("apply-brand-kit %s: [EXCEPTION] %s", config_key, traceback.format_exc())
                    _set_bk_apply_status(config_key, {
                        "status": "failed", "message": str(e)[:200],
                        "steps": (_get_bk_apply_status(config_key) or {}).get("steps", []),
                    })

        threading.Thread(target=_do_apply, daemon=True).start()
        logger.info("apply-brand-kit %s: background thread started, returning config_key to client", config_key)
        return jsonify({"code": 200, "message": "品牌套件应用已启动", "data": {"config_key": config_key}})

    @app.route("/api/sites/<int:site_id>/apply-brand-kit/status", methods=["GET"])
    @jwt_required()
    def get_apply_brand_kit_status_route(site_id):
        config_key = request.args.get("config_key", "")
        if config_key:
            s = _get_bk_apply_status(config_key)
            if s:
                return jsonify({"code": 200, "data": s})
        return jsonify({"code": 200, "data": {"status": "not_found"}})

    # ---- Unified Brand Config (AI + WooCommerce + Logo + Footer) ----
    brand_config_status = {}

    @app.route("/api/sites/<int:site_id>/brand-config", methods=["POST"])
    @jwt_required()
    def start_brand_config(site_id):
        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        data = request.get_json(silent=True) or {}
        brand_kit_id = data.get("brand_kit_id")
        brand_name = (data.get("brand_name") or "").strip()

        kit = None
        if brand_kit_id:
            kit = get_brand_kit(brand_kit_id)
            if not kit:
                return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
            if not brand_name:
                brand_name = kit.get("brand_name", "")

        if not brand_name:
            return jsonify({"code": 400, "message": "品牌名称不能为空"}), 400

        from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
        deepseek_keys = get_deepseek_keys()
        if not deepseek_keys:
            return jsonify({"code": 400, "message": "请先配置 DeepSeek API Key"}), 400

        config_key = str(uuid.uuid4())[:8]
        industry = kit.get("industry", "") if kit else ""
        woo_config_data = kit.get("woo_config", {}) if kit else {}
        footer_config_data = kit.get("footer_config", {}) if kit else {}
        tax_config_data = kit.get("tax_config", {}) if kit else {}
        shipping_config_data = kit.get("shipping_config", {}) if kit else {}
        business_info_data = kit.get("business_info", {}) if kit else {}

        brand_config_status[config_key] = {
            "status": "running",
            "message": "品牌配置已启动",
            "steps": [
                {"label": "AI 生成标题和副标题", "status": "pending", "message": ""},
                {"label": "AI 生成站点图标", "status": "pending", "message": ""},
                {"label": "设置网站标题和副标题", "status": "pending", "message": ""},
                {"label": "上传站点图标", "status": "pending", "message": ""},
                {"label": "注册及角色设置", "status": "pending", "message": ""},
                {"label": "时区设置", "status": "pending", "message": ""},
                {"label": "保存 WooCommerce 配置", "status": "pending", "message": ""},
                {"label": "上传品牌 Logo", "status": "pending", "message": ""},
                {"label": "设置站点 Logo", "status": "pending", "message": ""},
                {"label": "保存页脚信息", "status": "pending", "message": ""},
                {"label": "配置税率", "status": "pending", "message": ""},
                {"label": "配置免费配送", "status": "pending", "message": ""},
                {"label": "创建退货政策页面", "status": "pending", "message": ""},
            ],
        }

        def _do_brand_config():
            with app.app_context():
                try:
                    log_prefix = f"[品牌配置 {config_key}]"
                    wp = WordPressAdminSession(
                        site_url=f"http://{site['site_name']}",
                        username=site.get("admin_name", "admin"),
                        password=site.get("admin_password", ""),
                    )
                    # Retry WP login on DNS/proxy failure (up to 3 attempts, 10s apart)
                    login_ok = False
                    for login_attempt in range(3):
                        if wp.login():
                            login_ok = True
                            break
                        logger.warning("%s WP login attempt %d/3 failed, retrying in 10s...", log_prefix, login_attempt + 1)
                        time.sleep(10)
                    if not login_ok:
                        brand_config_status[config_key] = {"status": "failed", "message": "WordPress 登录失败(重试3次)", "steps": brand_config_status[config_key]["steps"]}
                        return

                    steps = brand_config_status[config_key]["steps"]

                    # --- Step 0: DeepSeek AI ---
                    industry_hint = f"\nIndustry: {industry}" if industry else ""
                    deepseek_prompt = f"""You are a WordPress site config expert. Generate English site title and tagline for a brand.

Brand name: {brand_name}{industry_hint}

Respond with strict JSON only (no markdown code blocks):
{{
  "site_title": "Site title in English (concise, attractive, include brand name, 5 words max)",
  "tagline": "Tagline in English (describe the brand, 10 words max)",
  "svg_logo": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>...minimal modern SVG icon</svg>"
}}"""

                    logger.info("%s Calling DeepSeek, brand=%s industry=%s", log_prefix, brand_name, industry)
                    steps[0]["status"] = "running"
                    brand_config_status[config_key]["message"] = "正在调用 AI 生成配置..."

                    def _ds_call(key):
                        return http_requests.post(
                            "https://api.deepseek.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                            json={
                                "model": "deepseek-chat",
                                "messages": [
                                    {"role": "system", "content": "你是一个专业的WordPress网站配置助手，只返回JSON格式数据。"},
                                    {"role": "user", "content": deepseek_prompt},
                                ],
                                "temperature": 0.8, "max_tokens": 4096,
                            },
                            timeout=90,
                        )

                    try:
                        resp = rotate_deepseek(_ds_call, deepseek_keys)
                    except Exception as e:
                        logger.error("%s DeepSeek API 全部密钥失败: %s", log_prefix, e)
                        brand_config_status[config_key] = {"status": "failed", "message": f"DeepSeek API 全部密钥失败: {e}", "steps": steps}
                        return

                    site_title = brand_name
                    tagline = ""
                    svg_logo = ""

                    if resp.status_code == 200:
                        result = resp.json()
                        ai_text = result["choices"][0]["message"]["content"].strip()
                        if "```" in ai_text:
                            ai_text = re.sub(r'^```(?:json)?\s*\n?', '', ai_text)
                            ai_text = re.sub(r'\n?```\s*$', '', ai_text)
                        try:
                            ai_data = json.loads(ai_text)
                        except json.JSONDecodeError:
                            json_match = re.search(r'\{[\s\S]*\}', ai_text)
                            ai_data = json.loads(json_match.group(0)) if json_match else {}
                        site_title = ai_data.get("site_title", brand_name)
                        tagline = ai_data.get("tagline", "")
                        svg_logo = ai_data.get("svg_logo", "")
                        steps[0]["status"] = "done"
                        steps[0]["message"] = f"标题: {site_title}"
                    else:
                        steps[0]["status"] = "done"
                        steps[0]["message"] = f"API失败, 使用品牌名: {site_title}"

                    steps[1]["status"] = "done"
                    steps[1]["message"] = "已生成" if svg_logo else "跳过"

                    # --- Set title & tagline ---
                    steps[2]["status"] = "running"
                    try:
                        ok = wp.update_settings({"title": site_title, "description": tagline})
                        steps[2]["status"] = "done" if ok else "failed"
                        steps[2]["message"] = site_title
                    except Exception as e:
                        steps[2]["status"] = "failed"; steps[2]["message"] = str(e)[:100]

                    # --- Upload site icon ---
                    steps[3]["status"] = "running"
                    if svg_logo and "<svg" in svg_logo.lower():
                        try:
                            icon_result = wp.upload_site_icon(svg_logo)
                            steps[3]["status"] = "done" if icon_result.get("success") else "failed"
                            steps[3]["message"] = f"ID:{icon_result.get('attachment_id')}" if icon_result.get("success") else icon_result.get("message", "")[:100]
                        except Exception as e:
                            steps[3]["status"] = "failed"; steps[3]["message"] = str(e)[:100]
                    else:
                        steps[3]["status"] = "done"; steps[3]["message"] = "跳过"

                    # --- Registration + role ---
                    steps[4]["status"] = "running"
                    try:
                        wp._post_options_page({"users_can_register": "1", "default_role": "customer"})
                        steps[4]["status"] = "done"
                        steps[4]["message"] = "注册已启用,默认角色customer"
                    except Exception as e:
                        steps[4]["status"] = "failed"; steps[4]["message"] = str(e)[:100]

                    # --- Timezone + site public ---
                    steps[5]["status"] = "running"
                    try:
                        ok = wp.update_settings({"timezone": "America/Chicago", "blog_public": 1})
                        steps[5]["status"] = "done" if ok else "failed"
                        steps[5]["message"] = "America/Chicago"
                    except Exception as e:
                        steps[5]["status"] = "failed"; steps[5]["message"] = str(e)[:100]

                    # --- WooCommerce config ---
                    steps[6]["status"] = "running"
                    try:
                        wc_settings = {"woocommerce_coming_soon": "no"}
                        # Merge address/city/postcode from woo_config, fallback to business_info
                        biz = business_info_data or {}
                        src_data = woo_config_data if woo_config_data else biz
                        for src, dest in [("address","woocommerce_store_address"),("city","woocommerce_store_city"),("postcode","woocommerce_store_postcode")]:
                            val = src_data.get(src) or biz.get(src)
                            if val:
                                wc_settings[dest] = val
                        # Country/State: from woo_config first, fallback to business_info
                        cs = woo_config_data.get("country_state") if woo_config_data else None
                        if not cs:
                            cs = biz.get("country_state")
                        if cs:
                            wc_settings["woocommerce_default_country"] = cs
                        # Allowed countries
                        ac = woo_config_data.get("allowed_countries") if woo_config_data else None
                        if ac:
                            wc_settings["woocommerce_allowed_countries"] = "specific"
                            wc_settings["woocommerce_specific_allowed_countries"] = [c.strip() for c in ac.split(",") if c.strip()]
                        else:
                            wc_settings["woocommerce_allowed_countries"] = "all"
                        # Site visibility: live mode
                        wc_settings["woocommerce_coming_soon"] = "no"
                        wp.update_woocommerce_settings("general", wc_settings)
                        steps[6]["status"] = "done"
                        steps[6]["message"] = f"{len(wc_settings)}项已保存"
                    except Exception as e:
                        steps[6]["status"] = "failed"; steps[6]["message"] = str(e)[:100]

                    # --- Upload brand logo ---
                    steps[7]["status"] = "running"
                    logo_att_id = None
                    if kit and kit.get("status") == "ready":
                        png_file = kit.get("png_512") or kit.get("png_256")
                        if png_file and kit.get("directory"):
                            png_path = os.path.join(os.path.dirname(__file__), kit["directory"], png_file)
                            if os.path.isfile(png_path):
                                try:
                                    up = wp.upload_image(png_path)
                                    if up.get("success"):
                                        logo_att_id = up.get("attachment_id")
                                        steps[7]["status"] = "done"
                                        steps[7]["message"] = f"ID:{logo_att_id}"
                                    else:
                                        steps[7]["status"] = "failed"; steps[7]["message"] = up.get("message","")[:100]
                                except Exception as e:
                                    steps[7]["status"] = "failed"; steps[7]["message"] = str(e)[:100]
                            else:
                                steps[7]["status"] = "done"; steps[7]["message"] = "文件不存在"
                        else:
                            steps[7]["status"] = "done"; steps[7]["message"] = "无PNG文件"
                    else:
                        steps[7]["status"] = "done"; steps[7]["message"] = "跳过"

                    # --- Set site logo ---
                    steps[8]["status"] = "running"
                    if logo_att_id:
                        try:
                            wp.set_site_logo(logo_att_id)
                            steps[8]["status"] = "done"
                            steps[8]["message"] = f"ID:{logo_att_id}"
                        except Exception as e:
                            steps[8]["status"] = "failed"; steps[8]["message"] = str(e)[:100]
                    else:
                        steps[8]["status"] = "done"; steps[8]["message"] = "跳过"

                    # --- Save footer ---
                    steps[9]["status"] = "running"
                    if footer_config_data:
                        addr = footer_config_data.get("address","")
                        phone = footer_config_data.get("phone","")
                        email = footer_config_data.get("email","")
                        if addr or phone or email:
                            try:
                                wp.save_footer_settings(addr, phone, email, logo_att_id)
                                steps[9]["status"] = "done"
                                steps[9]["message"] = "已保存"
                            except Exception as e:
                                steps[9]["status"] = "failed"; steps[9]["message"] = str(e)[:100]
                        else:
                            steps[9]["status"] = "done"; steps[9]["message"] = "跳过(空)"
                    else:
                        steps[9]["status"] = "done"; steps[9]["message"] = "跳过"

                    # --- Tax rates ---
                    steps[10]["status"] = "running"
                    if tax_config_data:
                        try:
                            ok = wp.setup_tax_rates(tax_config_data)
                            steps[10]["status"] = "done" if ok else "failed"
                            steps[10]["message"] = f"{len(tax_config_data.get('tax_rates',[]))}项税率" if ok else "税率配置失败"
                        except Exception as e:
                            steps[10]["status"] = "failed"; steps[10]["message"] = str(e)[:100]
                    else:
                        steps[10]["status"] = "done"; steps[10]["message"] = "跳过"

                    # --- Free shipping ---
                    steps[11]["status"] = "running"
                    if shipping_config_data:
                        try:
                            ok = wp.setup_free_shipping(shipping_config_data)
                            steps[11]["status"] = "done" if ok else "failed"
                            steps[11]["message"] = shipping_config_data.get("zone_name","免费配送") if ok else "配送配置失败"
                        except Exception as e:
                            steps[11]["status"] = "failed"; steps[11]["message"] = str(e)[:100]
                    else:
                        steps[11]["status"] = "done"; steps[11]["message"] = "跳过"

                    # --- Return policy page ---
                    steps[12]["status"] = "running"
                    try:
                        rp_title = f"Return Policy - {brand_name}"
                        rp_content = f"""<h2>Return Policy</h2>
<p>At <strong>{brand_name}</strong>, your satisfaction is our top priority. If you are not completely satisfied with your purchase, we're here to help.</p>

<h3>Return Window</h3>
<p>You have <strong>30 calendar days</strong> from the date of delivery to return an item.</p>

<h3>Eligibility</h3>
<ul>
<li>Items must be unused, unworn, and in the same condition that you received them.</li>
<li>Items must be in the original packaging with all tags and labels attached.</li>
<li>Proof of purchase or order confirmation is required.</li>
</ul>

<h3>Non-Returnable Items</h3>
<ul>
<li>Gift cards</li>
<li>Downloadable software products</li>
<li>Personal care items (if opened)</li>
<li>Custom-made or personalized items</li>
</ul>

<h3>Refunds</h3>
<p>Once we receive and inspect your return, we will notify you of the approval or rejection of your refund. If approved, your refund will be processed to your original method of payment within <strong>5-10 business days</strong>.</p>

<h3>Exchanges</h3>
<p>We only replace items if they are defective or damaged. If you need to exchange an item, please contact us at the email below.</p>

<h3>Shipping Costs for Returns</h3>
<p>You will be responsible for paying your own shipping costs for returning your item. Shipping costs are non-refundable. If you receive a refund, the cost of return shipping will be deducted from your refund.</p>

<h3>How to Initiate a Return</h3>
<p>To start a return, please contact our customer service team:</p>
<ul>
<li>Visit our <strong>Contact Us</strong> page</li>
<li>Email us with your order number and reason for return</li>
<li>We will provide you with return instructions and a return authorization number</li>
</ul>

<h3>Contact Us</h3>
<p>If you have any questions about our Return Policy, please do not hesitate to contact our support team.</p>"""
                        result = wp.create_page(rp_title, rp_content)
                        if result.get("success"):
                            steps[12]["status"] = "done"
                            steps[12]["message"] = f"页面已创建 (ID:{result.get('post_id', 'N/A')})"
                        else:
                            steps[12]["status"] = "failed"
                            steps[12]["message"] = result.get("message", "")[:100]
                    except Exception as e:
                        steps[12]["status"] = "failed"
                        steps[12]["message"] = str(e)[:100]

                    # Inject CloakBrowser profile to site if fingerprint is enabled
                    fingerprint_enabled = get_global_config().get("fingerprint_enabled", "false") == "true"
                    if fingerprint_enabled and kit and kit.get("cloakbrowser_profile_name"):
                        try:
                            update_site(site_id, {"cloakbrowser_profile_name": kit["cloakbrowser_profile_name"]})
                            logger.info("%s Injected profile '%s' to site %s", log_prefix, kit["cloakbrowser_profile_name"], site_id)
                        except Exception as e:
                            logger.warning("%s Failed to inject profile to site: %s", log_prefix, e)

                    all_done = all(s["status"] == "done" for s in steps)
                    brand_config_status[config_key]["status"] = "success" if all_done else "failed"
                    brand_config_status[config_key]["message"] = "品牌配置完成" if all_done else "部分步骤失败"
                    if all_done:
                        update_site(site_id, {"brand_configured": 1})
                    logger.info("%s Done all_done=%s", log_prefix, all_done)

                except Exception as e:
                    logger.error("%s Unhandled: %s\n%s", log_prefix, e, traceback.format_exc())
                    brand_config_status[config_key] = {
                        "status": "failed", "message": str(e)[:200],
                        "steps": brand_config_status.get(config_key, {}).get("steps", []),
                    }

        threading.Thread(target=_do_brand_config, daemon=True).start()
        return jsonify({"code": 200, "data": {"config_key": config_key}, "message": "品牌配置已启动"})

    @app.route("/api/sites/<int:site_id>/brand-config/status", methods=["GET"])
    @jwt_required()
    def get_brand_config_status(site_id):
        config_key = request.args.get("config_key", "")
        if not config_key:
            return jsonify({"code": 400, "message": "缺少 config_key"}), 400
        s = brand_config_status.get(config_key)
        if s:
            return jsonify({"code": 200, "data": s})
        return jsonify({"code": 200, "data": {"status": "not_found"}})

    @app.route("/api/sites/<int:site_id>/woo-status", methods=["GET"])
    @jwt_required()
    def get_woo_status(site_id):
        """Check if WooCommerce is installed and active."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败"}), 400
            active = wp.is_woocommerce_active()
            return jsonify({"code": 200, "data": {"active": active}})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/woo-install", methods=["POST"])
    @jwt_required()
    def install_woocommerce(site_id):
        """Install and activate WooCommerce plugin via unified WP.org install flow."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败"}), 400
            result = _install_wp_org_plugin(wp.session, f"http://{site['site_name']}", "woocommerce")
            return jsonify({
                "code": 200 if result["success"] else 500,
                "message": result["message"],
            })
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/woo-settings/<group>", methods=["GET"])
    @jwt_required()
    def get_woo_settings(site_id, group):
        """Get WooCommerce settings for a group (general/products/shipping/payments)."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败"}), 400

            settings = wp.get_woocommerce_settings(group)
            return jsonify({"code": 200, "data": settings})
        except Exception as e:
            logger.error(f"get_woo_settings: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/sites/<int:site_id>/woo-settings/<group>", methods=["POST"])
    @jwt_required()
    def update_woo_settings(site_id, group):
        """Update WooCommerce settings for a group."""
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404

            data = request.get_json(silent=True) or {}
            wp = WordPressAdminSession(
                site_url=f"http://{site['site_name']}",
                username=site.get("admin_name", "admin"),
                password=site.get("admin_password", ""),
            )
            if not wp.login():
                return jsonify({"code": 400, "message": "WordPress登录失败"}), 400

            ok = wp.update_woocommerce_settings(group, data)
            return jsonify({"code": 200 if ok else 500, "message": "已保存" if ok else "保存失败"})
        except Exception as e:
            logger.error(f"update_woo_settings: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- WordPress.com Integration ----

    @app.route("/api/wordpress-com/auth-url", methods=["GET"])
    @jwt_required()
    def wpcom_auth_url():
        """Get WordPress.com OAuth2 authorization URL.
        Client ID/Secret are configured via global_config or environment variables.
        """
        try:
            client_id = os.environ.get("WPCOM_CLIENT_ID", "")
            redirect_uri = os.environ.get("WPCOM_REDIRECT_URI", "")
            if not client_id:
                return jsonify({"code": 400, "message": "请在环境变量中配置 WPCOM_CLIENT_ID"}), 400
            if not redirect_uri:
                redirect_uri = request.host_url.rstrip("/") + "/api/wordpress-com/callback"
            url = WordPressComClient.get_auth_url(client_id, redirect_uri)
            return jsonify({"code": 200, "data": {"url": url}})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/wordpress-com/callback", methods=["GET", "POST"])
    def wpcom_callback():
        """Handle WordPress.com OAuth2 callback. Saves token to global_config."""
        try:
            code = request.args.get("code") or (request.get_json(silent=True) or {}).get("code")
            if not code:
                return jsonify({"code": 400, "message": "缺少授权码 code"}), 400

            client_id = os.environ.get("WPCOM_CLIENT_ID", "")
            client_secret = os.environ.get("WPCOM_CLIENT_SECRET", "")
            redirect_uri = os.environ.get("WPCOM_REDIRECT_URI", "")
            if not redirect_uri:
                redirect_uri = request.host_url.rstrip("/") + "/api/wordpress-com/callback"

            token_resp = WordPressComClient.exchange_code(
                client_id, client_secret, code, redirect_uri
            )
            access_token = token_resp.get("access_token")
            if not access_token:
                return jsonify({"code": 500, "message": f"换取token失败: {token_resp}"}), 500

            # Save to global_config
            db = get_db()
            now = datetime.utcnow().isoformat()
            db.execute(
                "INSERT INTO global_config (config_key, config_value, updated_at) VALUES ('wpcom_token', ?, ?) ON CONFLICT(config_key) DO UPDATE SET config_value = ?, updated_at = ?",
                (access_token, now, access_token, now),
            )
            db.execute(
                "INSERT INTO global_config (config_key, config_value, updated_at) VALUES ('wpcom_connected', ?, ?) ON CONFLICT(config_key) DO UPDATE SET config_value = ?, updated_at = ?",
                ("true", now, "true", now),
            )
            db.commit()

            # Validate and get user info
            wpcom = WordPressComClient(access_token=access_token)
            me_resp = wpcom.get_me()
            if "error" not in me_resp:
                db.execute(
                    "INSERT INTO global_config (config_key, config_value, updated_at) VALUES ('wpcom_email', ?, ?) ON CONFLICT(config_key) DO UPDATE SET config_value = ?, updated_at = ?",
                    (me_resp.get("email", ""), now, me_resp.get("email", ""), now),
                )
                db.commit()

            return redirect(f"{request.host_url.rstrip('/')}?wpcom_ok=1")
        except Exception as e:
            logger.error(f"WP.com callback error: {e}")
            return redirect(f"{request.host_url.rstrip('/')}?wpcom_ok=0&err={str(e)[:100]}")

    @app.route("/api/wordpress-com/status", methods=["GET"])
    @jwt_required()
    def wpcom_status():
        """Check WordPress.com connection status."""
        try:
            db = get_db()
            row = db.execute(
                "SELECT config_value FROM global_config WHERE config_key='wpcom_connected'"
            ).fetchone()
            connected = row and row[0] == "true"
            email = ""
            token = ""
            if connected:
                email_row = db.execute(
                    "SELECT config_value FROM global_config WHERE config_key='wpcom_email'"
                ).fetchone()
                email = email_row[0] if email_row else ""
                token_row = db.execute(
                    "SELECT config_value FROM global_config WHERE config_key='wpcom_token'"
                ).fetchone()
                token = token_row[0] if token_row else ""
            return jsonify({"code": 200, "data": {
                "connected": connected,
                "email": email,
                "has_token": bool(token),
            }})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/wordpress-com/bind-domain", methods=["POST"])
    @jwt_required()
    def wpcom_bind_domain():
        """Bind a custom domain to a WordPress.com site. Body: {domain}."""
        try:
            data = request.get_json(silent=True) or {}
            domain = data.get("domain", "").strip()
            if not domain:
                return jsonify({"code": 400, "message": "请提供域名"}), 400

            db = get_db()
            token_row = db.execute(
                                "SELECT config_value FROM global_config WHERE config_key='wpcom_token'"
            ).fetchone()
            if not token_row or not token_row[0]:
                return jsonify({"code": 400, "message": "请先在设置中连接 WordPress.com"}), 400

            wpcom = WordPressComClient(access_token=token_row[0])
            primary = wpcom.get_primary_site()
            if not primary:
                return jsonify({"code": 400, "message": "未找到 WordPress.com 站点，请先在 WordPress.com 创建站点"}), 400

            site_id = primary.get("ID") or primary.get("blog_id")
            resp = wpcom.map_domain(int(site_id), domain)
            if resp and "error" not in resp:
                return jsonify({"code": 200, "data": resp, "message": f"域名 {domain} 已提交绑定到 WordPress.com"})
            return jsonify({"code": 500, "message": f"域名绑定失败: {resp}"}), 500
        except Exception as e:
            logger.error(f"WP.com bind domain error: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- 筛品 - 沃尔玛商超 (Crawlbase) ----
    @app.route("/api/shai-pin/walmart/categories", methods=["GET"])
    @jwt_required()
    def walmart_categories():
        """List supported Walmart bestseller categories with cache info."""
        cats = CrawlbaseWalmartService.list_categories()
        # Attach cached product counts to sub-category items
        try:
            db_cats = list_walmart_categories_from_db()
            for group in cats:
                for it in group["items"]:
                    match = next((d for d in db_cats if d["category_key"] == it["key"]), None)
                    if match:
                        it["cached_count"] = match["product_count"]
                        it["cached_at"] = match["fetched_at"]
        except Exception:
            pass
        return jsonify({
            "code": 200,
            "data": cats,
        })

    @app.route("/api/shai-pin/walmart/fetch", methods=["POST"])
    @jwt_required()
    def walmart_fetch():
        """Fetch Walmart bestsellers for a given category via Crawlbase."""
        try:
            data = request.get_json(silent=True) or {}
            category_key = (data.get("category") or "").strip().lower()
            limit = int(data.get("limit", 100))

            if not category_key:
                return jsonify({"code": 400, "message": "请选择商品大类"}), 400

            from services.api_key_rotator import get_crawlbase_keys, rotate_crawlbase
            crawlbase_keys = get_crawlbase_keys()
            if not crawlbase_keys:
                return jsonify({
                    "code": 400,
                    "message": "Crawlbase Token 未配置，请在系统设置中配置或 .env 中设置 CRAWLBASE_TOKEN",
                }), 400

            products = rotate_crawlbase(
                lambda k: CrawlbaseWalmartService(token=k).fetch_category_bestsellers(category_key, limit=limit),
                crawlbase_keys,
            )

            # Persist to local database
            # Look up category_label from grouped category list
            category_label = category_key
            for g in CrawlbaseWalmartService.list_categories():
                for it in g["items"]:
                    if it["key"] == category_key:
                        category_label = it["label"]
                        break
            saved = save_walmart_products(category_key, category_label, products)
            logger.info(f"Saved {saved} Walmart products for {category_key}")

            return jsonify({
                "code": 200,
                "data": {
                    "category": category_key,
                    "category_label": category_label,
                    "count": len(products),
                    "products": products,
                },
            })
        except CrawlbaseAuthError as e:
            logger.warning(f"Walmart fetch auth error: {e}")
            return jsonify({"code": 403, "message": f"Crawlbase 认证失败: {str(e)[:100]}"}), 403
        except CrawlbaseRateLimitError as e:
            logger.warning(f"Walmart fetch rate-limit: {e}")
            return jsonify({"code": 429, "message": f"请求频率过高，请稍后重试: {str(e)[:100]}"}), 429
        except CrawlbaseParseError as e:
            logger.warning(f"Walmart fetch parse error: {e}")
            return jsonify({"code": 422, "message": f"页面解析失败: {str(e)[:100]}"}), 422
        except CrawlbaseTimeoutError as e:
            logger.warning(f"Walmart fetch timeout: {e}")
            return jsonify({"code": 504, "message": f"请求超时: {str(e)[:100]}"}), 504
        except WalmartServiceError as e:
            logger.warning(f"Walmart service error: {e}")
            return jsonify({"code": 502, "message": f"数据获取失败: {str(e)[:100]}"}), 502
        except Exception as e:
            logger.error(f"Walmart fetch unexpected error: {e}")
            return jsonify({"code": 500, "message": f"服务器内部错误: {str(e)[:100]}"}), 500

    @app.route("/api/shai-pin/walmart/products", methods=["GET"])
    @jwt_required()
    def walmart_load_products():
        """Load persisted Walmart products from the database."""
        try:
            category_key = (request.args.get("category") or "").strip().lower()
            products = load_walmart_products(category_key)
            return jsonify({
                "code": 200,
                "data": {
                    "category": category_key or "all",
                    "count": len(products),
                    "products": products,
                },
            })
        except Exception as e:
            logger.error(f"Walmart load products error: {e}")
            return jsonify({"code": 500, "message": f"加载数据失败: {str(e)[:100]}"}), 500

    @app.route("/api/shai-pin/walmart/enrich", methods=["POST"])
    @jwt_required()
    def walmart_enrich():
        """Batch-fetch product detail pages and save to generated_feed."""
        try:
            data = request.get_json(silent=True) or {}
            urls = data.get("urls") or []
            category = (data.get("category") or "").strip()

            if not urls:
                return jsonify({"code": 400, "message": "没有可处理的商品链接"}), 400

            from services.api_key_rotator import get_crawlbase_keys, rotate_crawlbase
            crawlbase_keys = get_crawlbase_keys()
            if not crawlbase_keys:
                return jsonify({"code": 400, "message": "Crawlbase Token 未配置，请在系统设置中配置"}), 400

            results = []
            total = len(urls)

            def _fetch_with_rotation(url):
                return rotate_crawlbase(
                    lambda k: CrawlbaseWalmartService(token=k, page_delay=1.5, timeout=60).fetch_product_detail(url),
                    crawlbase_keys,
                )

            for i, url in enumerate(urls):
                try:
                    detail = _fetch_with_rotation(url)
                    detail["category"] = category
                    rid = save_generated_feed_product(detail)
                    results.append({"ok": True, "id": rid, "title": detail["title"][:60]})
                except Exception as inner_e:
                    logger.warning(f"Enrich failed for {url[:80]}: {inner_e}")
                    results.append({"ok": False, "url": url[:80], "error": str(inner_e)[:100]})

            ok_count = sum(1 for r in results if r["ok"])
            return jsonify({
                "code": 200,
                "data": {
                    "total": total,
                    "ok": ok_count,
                    "fail": total - ok_count,
                    "results": results,
                },
            })
        except Exception as e:
            logger.error(f"Walmart enrich error: {e}")
            return jsonify({"code": 500, "message": f"数据异步失败: {str(e)[:100]}"}), 500

    @app.route("/api/shai-pin/feed/list", methods=["GET"])
    @jwt_required()
    def feed_list():
        """List generated feed products."""
        try:
            site_id = request.args.get("site_id", type=int)
            products = list_generated_feed(site_id)
            return jsonify({"code": 200, "data": products})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/feed/clear", methods=["DELETE"])
    @jwt_required()
    def feed_clear():
        """Clear all generated feed products."""
        try:
            count = clear_generated_feed()
            return jsonify({"code": 200, "message": f"已清除 {count} 条", "data": {"deleted": count}})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/feed/items", methods=["DELETE"])
    @jwt_required()
    def feed_items_delete():
        """Delete selected generated feed products."""
        try:
            data = request.get_json(silent=True) or {}
            ids = data.get("ids") or []
            if not ids:
                return jsonify({"code": 400, "message": "请选择要删除的产品"}), 400
            deleted = delete_generated_feed_items([int(i) for i in ids])
            return jsonify({"code": 200, "data": {"deleted": deleted}})
        except Exception as e:
            logger.error(f"Feed item delete error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/walmart/export", methods=["POST"])
    @jwt_required()
    def walmart_export():
        """Export fetched product data as JSON or Excel file."""
        try:
            body = request.get_json(silent=True) or {}
            export_format = (body.get("format") or "excel").strip().lower()
            data = body.get("data") or []
            category = (body.get("category") or "walmart").strip()

            if not data:
                return jsonify({"code": 400, "message": "没有可导出的数据"}), 400

            if export_format == "json":
                buf = DataExportUtil.export_to_json_bytes(data)
                return send_file(
                    buf,
                    mimetype="application/json",
                    as_attachment=True,
                    download_name=f"walmart_{category}_{_now_ts()}.json",
                )

            # Default: Excel
            buf = DataExportUtil.export_to_excel_bytes(data, sheet_name="Walmart热销")
            return send_file(
                buf,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=f"walmart_{category}_{_now_ts()}.xlsx",
            )
        except ValueError as e:
            return jsonify({"code": 400, "message": str(e)[:200]}), 400
        except ImportError as e:
            return jsonify({"code": 500, "message": f"缺少依赖: {str(e)}"}), 500
        except Exception as e:
            logger.error(f"Walmart export error: {e}")
            return jsonify({"code": 500, "message": f"导出失败: {str(e)[:100]}"}), 500

    # ---- 筛品 - Amazon 爆品导入 ----

    @app.route("/api/shai-pin/amazon/search", methods=["POST"])
    @jwt_required()
    def amazon_search():
        """Search Amazon for given product names via Crawlbase with streaming NDJSON."""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from queue import Queue
        from threading import Lock
        from urllib.parse import quote

        data = request.get_json(silent=True) or {}
        product_names = data.get("product_names") or []
        if not isinstance(product_names, list):
            product_names = [str(product_names).strip()]
        product_names = [n.strip() for n in product_names if n and n.strip()]

        if not product_names:
            return jsonify({"code": 400, "message": "请输入至少一个产品名称"}), 400

        from services.api_key_rotator import get_crawlbase_keys, rotate_crawlbase
        crawlbase_keys = get_crawlbase_keys()
        if not crawlbase_keys:
            return jsonify({"code": 400, "message": "Crawlbase Token 未配置，请在系统设置中配置"}), 400

        CRAWLBASE_API = "https://api.crawlbase.com/"
        total_names = len(product_names)
        seen_urls = set()
        seen_urls_lock = Lock()
        all_products = []
        products_lock = Lock()

        logger.info(f"[AmazonSearch] Starting search for {total_names} product names: {product_names}")

        def search_one(name, idx):
            """Search a single product name, return a list of products."""
            try:
                logger.info(f"[AmazonSearch] [{idx+1}/{total_names}] Searching: '{name}'")
                t0 = _time.time()
                search_url = f"https://www.amazon.com/s?k={quote(name)}"
                encoded_url = quote(search_url, safe="")

                resp = rotate_crawlbase(
                    lambda k: http_requests.get(f"{CRAWLBASE_API}?token={k}&url={encoded_url}&autoparse=true", timeout=90),
                    crawlbase_keys,
                )
                elapsed = _time.time() - t0
                logger.info(f"[AmazonSearch] [{idx+1}/{total_names}] HTTP {resp.status_code} for '{name}' in {elapsed:.1f}s")

                if resp.status_code != 200:
                    logger.warning(f"[AmazonSearch] [{idx+1}/{total_names}] Non-200 for '{name}': {resp.status_code}")
                    return []

                raw = resp.json()
                if not isinstance(raw, dict):
                    logger.warning(f"[AmazonSearch] [{idx+1}/{total_names}] Response not dict for '{name}'")
                    return []

                pc_status = raw.get("pc_status")
                if pc_status and pc_status >= 400:
                    logger.warning(f"[AmazonSearch] [{idx+1}/{total_names}] pc_status={pc_status} for '{name}'")
                    return []

                body = raw.get("body") if isinstance(raw.get("body"), dict) else raw
                if not isinstance(body, dict):
                    logger.warning(f"[AmazonSearch] [{idx+1}/{total_names}] body not dict for '{name}'")
                    return []

                search_products = _extract_amazon_search_products(body, name)
                logger.info(f"[AmazonSearch] [{idx+1}/{total_names}] Found {len(search_products)} products for '{name}'")

                # Keep only the top 1 result, deduplicate by source_url
                top = None
                for p in search_products:
                    url = p.get("source_url", "")
                    with seen_urls_lock:
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            top = p
                            break

                if top:
                    # Skip products without any price data
                    if not top.get("price") and not top.get("original_price"):
                        logger.info(f"[AmazonSearch] [{idx+1}/{total_names}] Skipped (no price): '{top.get('product_name','')[:60]}'")
                        return []
                    logger.info(f"[AmazonSearch] [{idx+1}/{total_names}] Picked top result: '{top.get('product_name','')[:60]}'")
                    return [top]
                return []
            except Exception as e:
                logger.warning(f"[AmazonSearch] [{idx+1}/{total_names}] Failed for '{name}': {e}")
                return []

        def generate():
            """Stream NDJSON as results complete."""
            yield json.dumps({"type": "start", "total": total_names}) + "\n"
            max_workers = min(5, total_names)
            logger.info(f"[AmazonSearch] Using {max_workers} threads for {total_names} queries")

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(search_one, name, idx): idx for idx, name in enumerate(product_names)}
                completed_count = 0

                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        products = future.result()
                        if products:
                            with products_lock:
                                all_products.extend(products)
                            yield json.dumps({
                                "type": "result",
                                "query_idx": idx,
                                "query_name": product_names[idx],
                                "count": len(products),
                                "products": products,
                                "total_so_far": len(all_products),
                            }) + "\n"
                            logger.info(f"[AmazonSearch] YIELDED {len(products)} products from '{product_names[idx]}' (total so far: {len(all_products)})")
                        else:
                            yield json.dumps({
                                "type": "result",
                                "query_idx": idx,
                                "query_name": product_names[idx],
                                "count": 0,
                                "products": [],
                                "total_so_far": len(all_products),
                            }) + "\n"
                    except Exception as e:
                        logger.error(f"[AmazonSearch] Future error for '{product_names[idx]}': {e}")
                        yield json.dumps({
                            "type": "result",
                            "query_idx": idx,
                            "query_name": product_names[idx],
                            "count": 0,
                            "products": [],
                            "error": str(e)[:100],
                            "total_so_far": len(all_products),
                        }) + "\n"
                    completed_count += 1

                logger.info(f"[AmazonSearch] All {completed_count}/{total_names} queries completed. Total unique products: {len(all_products)}")

                # Persist to database
                saved_count = 0
                if all_products:
                    try:
                        clear_amazon_search_results()
                        saved_count = save_amazon_search_results(all_products)
                        logger.info(f"[AmazonSearch] Persisted {saved_count} products to amazon_search_results")
                    except Exception as e:
                        logger.error(f"[AmazonSearch] Failed to save to DB: {e}")

                yield json.dumps({
                    "type": "done",
                    "query_count": total_names,
                    "result_count": len(all_products),
                    "saved_count": saved_count,
                }) + "\n"

        try:
            return Response(
                generate(),
                mimetype="application/x-ndjson",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache",
                },
            )
        except Exception as e:
            logger.error(f"[AmazonSearch] Stream setup error: {e}")
            return jsonify({"code": 500, "message": f"搜索失败: {str(e)[:100]}"}), 500

    @app.route("/api/shai-pin/amazon/direct-import", methods=["POST"])
    @jwt_required()
    def amazon_direct_import():
        """Import Amazon products directly from URLs (skip search). Streams NDJSON."""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from urllib.parse import urlparse

        data = request.get_json(silent=True) or {}
        urls = data.get("urls") or []
        if not isinstance(urls, list):
            urls = [str(urls).strip()]
        urls = [u.strip() for u in urls if u and u.strip()]

        if not urls:
            return jsonify({"code": 400, "message": "请输入至少一个 Amazon 产品链接"}), 400

        from services.api_key_rotator import get_crawlbase_keys
        crawlbase_keys = get_crawlbase_keys()
        if not crawlbase_keys:
            return jsonify({"code": 400, "message": "Crawlbase Token 未配置"}), 400

        total = len(urls)
        logger.info(f"[AmazonDirectImport] Starting direct import of {total} URLs")

        def fetch_one(url, idx):
            try:
                logger.info(f"[AmazonDirectImport] [{idx+1}/{total}] Fetching: {url[:120]}")
                detail = _fetch_amazon_product_detail(crawlbase_keys, url)
                # Build product dict compatible with search results format
                extra = detail.get("extra_data") or {}
                price_str = detail.get("price") or extra.get("originalPrice") or "0.00"
                product = {
                    "product_name": detail.get("title", ""),
                    "price": detail.get("price", ""),
                    "source_url": url,
                    "thumbnail": detail.get("thumbnail", ""),
                    "rating_score": _safe_float(detail.get("ratings", "")) if detail.get("ratings") else 0,
                    "review_count": detail.get("reviews_count", 0),
                    "search_query": urlparse(url).path.split("/")[1] if urlparse(url).path else "",
                    "asin": detail.get("item_id", ""),
                    "brand": detail.get("brand", ""),
                    "breadcrumbs": " > ".join(detail.get("breadcrumbs") or []) if isinstance(detail.get("breadcrumbs"), list) else "",
                    "features": "||".join(str(f) for f in (detail.get("features") or [])) if isinstance(detail.get("features"), list) else "",
                    "original_price": extra.get("originalPrice", ""),
                    "is_prime": extra.get("isPrime", False),
                    "delivery": extra.get("deliveryInfo", ""),
                }
                logger.info(f"[AmazonDirectImport] [{idx+1}/{total}] OK '{product['product_name'][:50]}'")
                return {"ok": True, "idx": idx, "product": product}
            except Exception as e:
                logger.warning(f"[AmazonDirectImport] [{idx+1}/{total}] Failed: {e}")
                return {"ok": False, "idx": idx, "error": str(e)[:100]}

        def generate():
            yield json.dumps({"type": "start", "total": total}) + "\n"
            max_workers = min(3, total)
            all_products = []

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(fetch_one, url, idx): idx for idx, url in enumerate(urls)}
                ok_count = 0
                fail_count = 0
                completed = 0

                for future in as_completed(futures):
                    completed += 1
                    try:
                        r = future.result()
                        if r["ok"]:
                            ok_count += 1
                            all_products.append(r["product"])
                            yield json.dumps({
                                "type": "result",
                                "query_idx": r["idx"],
                                "query_name": urls[r["idx"]][:80],
                                "count": 1,
                                "products": [r["product"]],
                                "total_so_far": len(all_products),
                            }) + "\n"
                        else:
                            fail_count += 1
                            yield json.dumps({
                                "type": "result",
                                "query_idx": r["idx"],
                                "query_name": urls[r["idx"]][:80],
                                "count": 0,
                                "products": [],
                                "error": r.get("error", ""),
                                "total_so_far": len(all_products),
                            }) + "\n"
                    except Exception as e:
                        fail_count += 1
                        logger.error(f"[AmazonDirectImport] Future error: {e}")

                # Persist to database
                saved_count = 0
                if all_products:
                    try:
                        clear_amazon_search_results()
                        saved_count = save_amazon_search_results(all_products)
                        logger.info(f"[AmazonDirectImport] Persisted {saved_count} products")
                    except Exception as e:
                        logger.error(f"[AmazonDirectImport] Failed to save: {e}")

                yield json.dumps({
                    "type": "done",
                    "query_count": total,
                    "result_count": len(all_products),
                    "saved_count": saved_count,
                }) + "\n"

        try:
            return Response(
                generate(),
                mimetype="application/x-ndjson",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache",
                },
            )
        except Exception as e:
            logger.error(f"[AmazonDirectImport] Stream setup error: {e}")
            return jsonify({"code": 500, "message": f"导入失败: {str(e)[:100]}"}), 500

    @app.route("/api/shai-pin/amazon/search-results", methods=["GET"])
    @jwt_required()
    def amazon_search_results_load():
        """Load persisted Amazon search results."""
        try:
            products = load_amazon_search_results()
            return jsonify({"code": 200, "data": products})
        except Exception as e:
            logger.error(f"Amazon load search results error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/amazon/search-results", methods=["DELETE"])
    @jwt_required()
    def amazon_search_results_delete():
        """Delete selected Amazon search results."""
        try:
            data = request.get_json(silent=True) or {}
            ids = data.get("ids") or []
            if not ids:
                return jsonify({"code": 400, "message": "请选择要删除的产品"}), 400
            deleted = delete_amazon_search_results([int(i) for i in ids])
            logger.info(f"[AmazonSearch] Deleted {deleted} search results")
            return jsonify({"code": 200, "data": {"deleted": deleted}})
        except Exception as e:
            logger.error(f"Amazon delete search results error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/amazon/convert", methods=["POST"])
    @jwt_required()
    def amazon_convert_to_feed():
        """Fetch product detail pages and save to generated_feed with streaming NDJSON."""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        data = request.get_json(silent=True) or {}
        products = data.get("products") or []
        if not products:
            return jsonify({"code": 400, "message": "没有可转换的产品"}), 400

        from services.api_key_rotator import get_crawlbase_keys
        crawlbase_keys = get_crawlbase_keys()
        if not crawlbase_keys:
            return jsonify({"code": 400, "message": "Crawlbase Token 未配置，请在系统设置中配置"}), 400

        total = len(products)
        logger.info(f"[AmazonConvert] Starting conversion of {total} products with streaming")

        def convert_one(p, idx):
            """Fetch detail for one product and save to feed."""
            source_url = p.get("source_url", "")
            if not source_url:
                return {"ok": False, "idx": idx, "title": p.get("product_name", "")[:60], "error": "缺少链接"}
            try:
                logger.info(f"[AmazonConvert] [{idx+1}/{total}] Fetching: {source_url[:80]}")
                detail = _fetch_amazon_product_detail(crawlbase_keys, source_url)
                detail["category"] = "amazon_hot_import"
                rid = save_generated_feed_product(detail)
                logger.info(f"[AmazonConvert] [{idx+1}/{total}] OK id={rid}: '{detail['title'][:50]}'")
                return {"ok": True, "idx": idx, "id": rid, "title": detail["title"][:60]}
            except Exception as e:
                logger.warning(f"[AmazonConvert] [{idx+1}/{total}] Failed: {e}")
                return {"ok": False, "idx": idx, "title": p.get("product_name", "")[:60], "error": str(e)[:100]}

        def generate():
            """Stream NDJSON lines as conversions complete."""
            yield json.dumps({"type": "start", "total": total}) + "\n"
            max_workers = min(3, total)
            logger.info(f"[AmazonConvert] Using {max_workers} threads")

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(convert_one, p, idx): idx for idx, p in enumerate(products)}
                ok_count = 0
                fail_count = 0
                completed = 0

                for future in as_completed(futures):
                    completed += 1
                    try:
                        r = future.result()
                        if r["ok"]:
                            ok_count += 1
                        else:
                            fail_count += 1
                        yield json.dumps({
                            "type": "log",
                            "completed": completed,
                            "total": total,
                            "ok": ok_count,
                            "fail": fail_count,
                            "entry": r,
                        }) + "\n"
                    except Exception as e:
                        fail_count += 1
                        yield json.dumps({
                            "type": "log",
                            "completed": completed,
                            "total": total,
                            "ok": ok_count,
                            "fail": fail_count,
                            "entry": {"ok": False, "idx": futures[future], "title": "Unknown", "error": str(e)[:100]},
                        }) + "\n"

                logger.info(f"[AmazonConvert] Done: {ok_count} ok / {fail_count} fail")
                yield json.dumps({
                    "type": "done",
                    "total": total,
                    "ok": ok_count,
                    "fail": fail_count,
                }) + "\n"

        try:
            return Response(
                generate(),
                mimetype="application/x-ndjson",
                headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
            )
        except Exception as e:
            logger.error(f"[AmazonConvert] Stream error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    # === WooCommerce Product Conversion ===

    @app.route("/api/shai-pin/woocommerce/convert", methods=["POST"])
    @jwt_required()
    def woocommerce_convert():
        """Fetch Amazon product details via Crawlbase, then convert to WooCommerce format with streaming NDJSON."""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        data = request.get_json(silent=True) or {}
        products = data.get("products") or []
        if not products:
            return jsonify({"code": 400, "message": "没有可转换的产品"}), 400

        from services.api_key_rotator import get_crawlbase_keys
        crawlbase_keys = get_crawlbase_keys()
        if not crawlbase_keys:
            return jsonify({"code": 400, "message": "Crawlbase Token 未配置"}), 400

        total = len(products)
        logger.info(f"[WooConvert] Starting conversion of {total} products with streaming")

        def _wc_one(p, idx):
            """Fetch detail for one product, convert to WooCommerce format, and save."""
            source_url = p.get("source_url", "")
            product_name = p.get("product_name", "")[:60]
            if not source_url:
                return {"ok": False, "idx": idx, "title": product_name, "error": "缺少链接"}
            try:
                logger.info(f"[WooConvert] [{idx+1}/{total}] Fetching: {source_url[:80]}")
                detail = _fetch_amazon_product_detail(crawlbase_keys, source_url)

                # Convert detail to WooCommerce format
                name = detail.get("title", product_name)
                price_str = (detail.get("price") or "").replace("$", "").replace(",", "").strip()
                try:
                    regular_price = str(float(price_str)) if price_str else ""
                except ValueError:
                    regular_price = price_str

                # Early rejection: skip products without title
                if not name:
                    return {"ok": False, "idx": idx, "title": "(no title)", "error": "no_title"}

                description = detail.get("description", "")
                features = detail.get("features") or []
                if features:
                    description += "\n\n特性:\n" + "\n".join(f"· {f}" for f in features)

                short_desc = (detail.get("description") or "")[:250]
                bc = detail.get("breadcrumbs") or []
                categories = " > ".join(bc) if isinstance(bc, list) and bc else ""
                imgs = detail.get("images") or []
                images = "|".join(str(i) for i in (imgs if isinstance(imgs, list) else []) if i)
                extra = detail.get("extra_data") or {}
                avail = (extra.get("availability") or "").lower()
                stock = "instock"
                if "out of stock" in avail or "unavailable" in avail:
                    stock = "outofstock"
                elif "backorder" in avail:
                    stock = "onbackorder"

                # --- Variant detection & processing ---
                variants_raw = extra.get("variants") if isinstance(extra.get("variants"), list) else []
                product_type = "simple"
                attributes = []
                variations_list = []

                # Determine if this is a variable product: multiple variants, or single variant with attributes
                if variants_raw:
                    has_attrs = any(
                        isinstance(v, dict) and v.get("attributes")
                        for v in variants_raw
                    )
                    if len(variants_raw) > 1 or (len(variants_raw) == 1 and has_attrs):
                        product_type = "variable"
                    # Collect all attribute names across variants
                    attr_names = set()
                    for v in variants_raw:
                        v_attrs = v.get("attributes") if isinstance(v, dict) else {}
                        if isinstance(v_attrs, dict):
                            attr_names.update(v_attrs.keys())
                        elif isinstance(v_attrs, list):
                            for a in v_attrs:
                                if isinstance(a, dict):
                                    attr_names.add(str(a.get("name") or a.get("key") or a.get("label") or ""))
                    attr_names.discard("")

                    # Build WC attributes with unique option values
                    for aname in sorted(attr_names):
                        options = set()
                        for v in variants_raw:
                            v_attrs = v.get("attributes") if isinstance(v, dict) else {}
                            if isinstance(v_attrs, dict):
                                val = v_attrs.get(aname)
                            elif isinstance(v_attrs, list):
                                val = None
                                for a in v_attrs:
                                    if isinstance(a, dict) and (a.get("name") or a.get("key") or "") == aname:
                                        val = a.get("value") or a.get("option")
                                        break
                            else:
                                val = None
                            if val:
                                options.add(str(val).strip())
                        if options:
                            attributes.append({
                                "name": aname,
                                "visible": True,
                                "variation": True,
                                "options": sorted(options),
                            })

                    # Build variation list
                    for v in variants_raw:
                        v_attrs = v.get("attributes") if isinstance(v, dict) else {}
                        v_price = (
                            v.get("price") or v.get("salePrice") or v.get("rawPrice")
                            or v.get("currentPrice") or ""
                        ).replace("$", "").replace(",", "").strip()
                        v_sku = str(v.get("asin") or v.get("id") or v.get("sku") or "").strip()
                        v_image = v.get("image") or v.get("thumbnail") or ""
                        v_avail = (v.get("availability") or "").lower()
                        v_stock = "instock"
                        if "out of stock" in v_avail or "unavailable" in v_avail:
                            v_stock = "outofstock"

                        v_wc_attrs = []
                        if isinstance(v_attrs, dict):
                            for k, val in v_attrs.items():
                                v_wc_attrs.append({"name": str(k).strip(), "option": str(val).strip()})
                        elif isinstance(v_attrs, list):
                            for a in v_attrs:
                                if isinstance(a, dict):
                                    v_wc_attrs.append({
                                        "name": str(a.get("name") or a.get("key") or "").strip(),
                                        "option": str(a.get("value") or a.get("option") or "").strip(),
                                    })

                        if v_wc_attrs:
                            variations_list.append({
                                "sku": v_sku,
                                "regular_price": v_price,
                                "attributes": v_wc_attrs,
                                "stock_status": v_stock,
                                "image": v_image,
                            })

                    logger.info(
                        f"[WooConvert] [{idx+1}/{total}] Variable product: "
                        f"{len(attributes)} attributes, {len(variations_list)} variations"
                    )
                    extra["product_type"] = "variable"
                    extra["attributes"] = attributes
                    extra["variations"] = variations_list

                # Price validation: simple products use 0.00 if price missing
                if product_type == "simple" and not regular_price:
                    regular_price = "0.00"
                if product_type == "variable" and variations_list:
                    priced_count = sum(1 for v in variations_list if v.get("regular_price"))
                    if priced_count == 0:
                        # All variants lack prices — try to inherit parent price for each variant
                        if regular_price:
                            for v in variations_list:
                                if not v.get("regular_price"):
                                    v["regular_price"] = regular_price
                        else:
                            return {"ok": False, "idx": idx, "title": name[:60], "error": "no_variant_prices"}

                wc = {
                    "name": name,
                    "sku": detail.get("item_id", ""),
                    "regular_price": regular_price,
                    "sale_price": str(extra.get("originalPrice", "")).replace("$", "").replace(",", "").strip() if extra.get("originalPrice") else "",
                    "description": description,
                    "short_description": short_desc,
                    "categories": categories,
                    "tags": detail.get("brand", ""),
                    "images": images,
                    "stock_status": stock,
                    "brand": detail.get("brand", ""),
                    "source_url": source_url,
                    "extra_data": extra,
                }

                rid = save_woocommerce_product(wc)
                logger.info(f"[WooConvert] [{idx+1}/{total}] OK id={rid}: '{name[:50]}'")
                return {"ok": True, "idx": idx, "id": rid, "title": name[:60]}
            except Exception as e:
                logger.warning(f"[WooConvert] [{idx+1}/{total}] Failed: {e}")
                return {"ok": False, "idx": idx, "title": product_name, "error": str(e)[:100]}

        def generate():
            yield json.dumps({"type": "start", "total": total}) + "\n"
            max_workers = min(3, total)
            logger.info(f"[WooConvert] Using {max_workers} threads")

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_wc_one, p, idx): idx for idx, p in enumerate(products)}
                ok_count = 0
                fail_count = 0
                completed = 0

                for future in as_completed(futures):
                    completed += 1
                    try:
                        r = future.result()
                        if r["ok"]:
                            ok_count += 1
                        else:
                            fail_count += 1
                        yield json.dumps({
                            "type": "log",
                            "completed": completed,
                            "total": total,
                            "ok": ok_count,
                            "fail": fail_count,
                            "entry": r,
                        }) + "\n"
                    except Exception as e:
                        fail_count += 1
                        yield json.dumps({
                            "type": "log",
                            "completed": completed,
                            "total": total,
                            "ok": ok_count,
                            "fail": fail_count,
                            "entry": {"ok": False, "idx": futures[future], "title": "Unknown", "error": str(e)[:100]},
                        }) + "\n"

                logger.info(f"[WooConvert] Done: {ok_count} ok / {fail_count} fail")
                yield json.dumps({
                    "type": "done",
                    "total": total,
                    "ok": ok_count,
                    "fail": fail_count,
                }) + "\n"

        try:
            return Response(
                generate(),
                mimetype="application/x-ndjson",
                headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
            )
        except Exception as e:
            logger.error(f"[WooConvert] Stream error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/woocommerce/products", methods=["GET"])
    @jwt_required()
    def woocommerce_products_list():
        """List WooCommerce products. Filter by site_id query param."""
        try:
            site_id = request.args.get("site_id", type=int)
            products = list_woocommerce_products(site_id)
            return jsonify({"code": 200, "data": products})
        except Exception as e:
            logger.error(f"WooCommerce list error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/woocommerce/products", methods=["DELETE"])
    @jwt_required()
    def woocommerce_products_delete():
        """Delete selected WooCommerce products."""
        try:
            data = request.get_json(silent=True) or {}
            ids = data.get("ids") or []
            if not ids:
                return jsonify({"code": 400, "message": "请选择要删除的产品"}), 400
            deleted = delete_woocommerce_products([int(i) for i in ids])
            logger.info(f"[WooCommerce] Deleted {deleted} products")
            return jsonify({"code": 200, "data": {"deleted": deleted}})
        except Exception as e:
            logger.error(f"WooCommerce delete error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    # === Feed / WooCommerce Site Sync ===

    @app.route("/api/shai-pin/feed/sync-to-site", methods=["POST"])
    @jwt_required()
    def feed_sync_to_site():
        """Generate GMC feed XML and upload to site. Static: 1Panel file API. WP: WordPress API."""
        import xml.etree.ElementTree as ET

        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        if not site_id:
            return jsonify({"code": 400, "message": "请选择目标站点"}), 400

        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        products = list_generated_feed(site_id)
        if not products:
            return jsonify({"code": 400, "message": "没有 Feed 产品可同步"}), 400

        # For static sites: upload feed.xml directly to 1Panel site directory
        if site.get("site_type") == "static":
            return _sync_feed_to_static_site(site, products)

        # WordPress site (legacy)
        try:
            ns_g = "http://base.google.com/ns/1.0"
            rss = ET.Element("rss", {"version": "2.0", "xmlns:g": ns_g})
            channel = ET.SubElement(rss, "channel")
            ET.SubElement(channel, "title").text = site.get("site_name") or site["url"]
            ET.SubElement(channel, "link").text = site["url"]
            ET.SubElement(channel, "description").text = "Google Shopping Product Feed"

            import json as _j

            for p in products:
                item = ET.SubElement(channel, "item")
                ET.SubElement(item, "g:id").text = str(p.get("id", ""))
                ET.SubElement(item, "g:title").text = (p.get("title") or "")[:150]
                desc = (p.get("description") or "")[:5000]
                ET.SubElement(item, "g:description").text = desc
                ET.SubElement(item, "g:link").text = p.get("source_url") or site["url"]

                images = p.get("images") or []
                if isinstance(images, str):
                    try:
                        images = _j.loads(images)
                    except Exception:
                        images = [images] if images else []
                if isinstance(images, list) and images:
                    ET.SubElement(item, "g:image_link").text = str(images[0])
                    for img in images[1:11]:
                        ET.SubElement(item, "g:additional_image_link").text = str(img)

                price = (p.get("price") or "").replace("$", "").replace(",", "").strip()
                currency = p.get("currency", "USD")
                if price:
                    ET.SubElement(item, "g:price").text = f"{price} {currency}"

                extra = p.get("extra_data") or {}
                if isinstance(extra, str):
                    try:
                        extra = _j.loads(extra)
                    except Exception:
                        extra = {}
                avail = (extra.get("availability") or "").lower()
                g_avail = "in_stock"
                if "out of stock" in avail or "unavailable" in avail:
                    g_avail = "out_of_stock"
                elif "backorder" in avail:
                    g_avail = "preorder"
                ET.SubElement(item, "g:availability").text = g_avail
                ET.SubElement(item, "g:condition").text = "new"

                brand = p.get("brand", "")
                if brand:
                    ET.SubElement(item, "g:brand").text = str(brand)[:70]

                sku = p.get("item_id", "")
                if sku:
                    ET.SubElement(item, "g:mpn").text = str(sku)[:70]

                breadcrumbs = p.get("breadcrumbs") or []
                if isinstance(breadcrumbs, str):
                    try:
                        breadcrumbs = _j.loads(breadcrumbs)
                    except Exception:
                        breadcrumbs = []
                if isinstance(breadcrumbs, list) and breadcrumbs:
                    ET.SubElement(item, "g:product_type").text = " > ".join(str(b) for b in breadcrumbs)[:750]

            xml_str = ET.tostring(rss, encoding="unicode")
            size_bytes = len(xml_str.encode("utf-8"))

            # Primary: upload to the selected WordPress site
            wp_feed_url = ""
            wp_error = ""
            try:
                wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
                wp_feed_url = wp.upload_feed_content(xml_str) or ""
                if wp_feed_url:
                    logger.info(f"[FeedSync] Uploaded to WP site {site_id}: {wp_feed_url} ({size_bytes} bytes)")
                    from models import update_site
                    update_site(site_id, {"google_feed_url": wp_feed_url})
                    return jsonify({
                        "code": 200,
                        "data": {"feed_url": wp_feed_url, "products": len(products), "size_bytes": size_bytes},
                    })
                else:
                    wp_error = "WordPress 上传返回空"
            except Exception as e:
                wp_error = str(e)[:200]
                logger.warning(f"[FeedSync] WP upload failed, falling back to local: {e}")

            # Fallback: save locally and serve from our public URL
            data_dir = os.environ.get("WP_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
            feed_dir = os.path.join(data_dir, "feeds")
            os.makedirs(feed_dir, exist_ok=True)
            with open(os.path.join(feed_dir, f"{site_id}.xml"), "w", encoding="utf-8") as f:
                f.write(xml_str)

            panel_host = current_app.config.get("PANEL_HOST", "").strip()
            wp_port = os.environ.get("WP_PORT", "8011")
            if panel_host:
                local_feed_url = f"http://{panel_host}:{wp_port}/api/public/feed/{site_id}.xml"
            else:
                local_feed_url = f"{request.host_url.rstrip('/')}/api/public/feed/{site_id}.xml"

            logger.info(f"[FeedSync] Fallback local URL: {local_feed_url} ({size_bytes} bytes)")
            from models import update_site
            update_site(site_id, {"google_feed_url": local_feed_url})
            return jsonify({
                "code": 200,
                "data": {"feed_url": local_feed_url, "products": len(products), "size_bytes": size_bytes},
            })
        except Exception as e:
            logger.error(f"[FeedSync] Error: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/shai-pin/feed/sync-to-site", methods=["DELETE"])
    @jwt_required()
    def feed_clean_from_site():
        """Remove Google feed file from WordPress site and local storage."""
        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        if not site_id:
            return jsonify({"code": 400, "message": "请选择目标站点"}), 400

        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        is_static = site.get("site_type") == "static"

        if is_static:
            cleaned = _clean_feed_from_static_site(site)
            if cleaned:
                logger.info(f"[FeedSync] Removed feed from static site {site_id}")
                return jsonify({"code": 200, "data": {"cleaned": True, "message": "已清理"}})
        else:
            cleaned = False
            try:
                wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
                if wp.delete_feed_file():
                    logger.info(f"[FeedSync] Removed feed from site {site_id}")
                    cleaned = True
            except Exception as e:
                logger.warning(f"[FeedSync] WP clean error (continuing): {e}")

        # Clean local feed file (all site types)
        data_dir = os.environ.get("WP_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
        feed_file = os.path.join(data_dir, "feeds", f"{site_id}.xml")
        if os.path.isfile(feed_file):
            try:
                os.remove(feed_file)
                logger.info(f"[FeedSync] Removed local feed: {feed_file}")
                cleaned = True
            except OSError as e:
                logger.warning(f"[FeedSync] Local clean error: {e}")

        return jsonify({"code": 200, "data": {"cleaned": cleaned, "message": "已清理" if cleaned else "没有需要清理的Feed文件"}})

    @app.route("/api/shai-pin/woocommerce/sync-to-site", methods=["POST"])
    @jwt_required()
    def woocommerce_sync_to_site():
        """Push products to site. WordPress: via WP API. Static: import to DB + regenerate HTML."""
        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        if not site_id:
            return jsonify({"code": 400, "message": "请选择目标站点"}), 400

        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        products = list_woocommerce_products(site_id)
        if not products:
            return jsonify({"code": 400, "message": "没有产品可同步"}), 400

        # Static site: import to local DB + regenerate
        if site.get("site_type") == "static":
            import json as _j
            mapped = []
            for p in products:
                images = p.get("images") or []
                if isinstance(images, str):
                    try: images = _j.loads(images)
                    except Exception: images = [images] if images else []
                # Clean price string: remove $, commas, and unit suffixes like "/count", "per oz", etc.
                def _clean_price(v):
                    if not v: return 0.0
                    v = str(v).replace("$", "").replace(",", "").strip()
                    # Take only the numeric part before any slash or alphabetic suffix
                    import re
                    m = re.match(r'^[\d.]+', v)
                    return float(m.group()) if m else 0.0

                mapped.append({
                    "title": p.get("name", ""),
                    "description": p.get("description", "") or p.get("short_description", ""),
                    "price": _clean_price(p.get("regular_price")),
                    "sale_price": _clean_price(p.get("sale_price")) if p.get("sale_price") else None,
                    "currency": "USD",
                    "image_url": images[0] if images else "",
                    "additional_images": images[1:] if len(images) > 1 else [],
                    "category": p.get("categories", ""),
                    "brand": p.get("brand", ""),
                    "sku": p.get("sku", ""),
                    "mpn": p.get("item_id", ""),
                    "product_url": p.get("source_url", ""),
                })
            count = import_products_to_site(site_id, mapped)
            # Regenerate the site HTML
            try:
                _regenerate_static_site_html(None, site_id)
            except Exception as re:
                logger.warning(f"Regenerate after sync failed: {re}")
            return jsonify({
                "code": 200,
                "data": {"ok": count, "fail": 0, "total": len(products)},
            })

        # WordPress site (legacy)
        try:
            wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
            result = wp.create_woocommerce_products(products)
            logger.info(f"[WooSync] Site {site_id}: {result}")
            return jsonify({
                "code": 200,
                "data": {
                    "ok": result.get("ok", 0),
                    "fail": result.get("fail", 0),
                    "total": len(products),
                },
            })
        except Exception as e:
            logger.error(f"[WooSync] Error: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/shai-pin/woocommerce/generate-feed", methods=["POST"])
    @jwt_required()
    def woocommerce_generate_feed():
        """Convert saved WooCommerce products to Google Shopping Feed format."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        site_id = request.args.get("site_id", type=int) or (request.get_json(silent=True) or {}).get("site_id")
        products = list_woocommerce_products(site_id)
        if not products:
            return jsonify({"code": 400, "message": "没有 WooCommerce 产品可生成 Feed"}), 400

        total = len(products)
        logger.info(f"[WooFeedGen] Converting {total} Woo products to Feed format")

        def convert_one(p, idx):
            try:
                extra = p.get("extra_data") or {}
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except Exception:
                        extra = {}

                images = []
                imgs_str = p.get("images", "")
                if imgs_str:
                    images = [u.strip() for u in imgs_str.split("|") if u.strip()]

                bc = []
                cat_str = p.get("categories", "")
                if cat_str:
                    bc = [c.strip() for c in cat_str.split(">") if c.strip()]

                features = []
                desc = p.get("description", "") or ""
                if desc and "特性:" in desc:
                    parts = desc.split("特性:", 1)
                    if len(parts) > 1:
                        features = [f.strip().lstrip("· ") for f in parts[1].split("\n") if f.strip().startswith("·")]

                feed_item = {
                    "title": p.get("name", ""),
                    "price": p.get("regular_price", ""),
                    "currency": "USD",
                    "brand": p.get("brand", ""),
                    "item_id": p.get("sku", ""),
                    "ratings": extra.get("ratings", ""),
                    "reviews_count": extra.get("reviews_count", 0),
                    "description": p.get("description", "")[:5000],
                    "images": images,
                    "features": features,
                    "breadcrumbs": bc,
                    "thumbnail": images[0] if images else "",
                    "source_url": p.get("source_url", ""),
                    "category": "woocommerce_products",
                    "extra_data": extra,
                }
                rid = save_generated_feed_product({**feed_item, "site_id": site_id})
                logger.info(f"[WooFeedGen] [{idx+1}/{total}] OK id={rid}: '{p.get('name','')[:50]}'")
                return {"ok": True, "idx": idx, "id": rid, "title": p.get("name", "")[:60]}
            except Exception as e:
                logger.warning(f"[WooFeedGen] [{idx+1}/{total}] Failed: {e}")
                return {"ok": False, "idx": idx, "title": p.get("name", "")[:60], "error": str(e)[:100]}

        def generate():
            yield json.dumps({"type": "start", "total": total}) + "\n"
            max_workers = min(5, total)
            ok_count = 0
            fail_count = 0
            completed = 0

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(convert_one, p, idx): idx for idx, p in enumerate(products)}

                for future in as_completed(futures):
                    completed += 1
                    try:
                        r = future.result()
                        if r["ok"]:
                            ok_count += 1
                        else:
                            fail_count += 1
                        yield json.dumps({
                            "type": "log",
                            "completed": completed,
                            "total": total,
                            "ok": ok_count,
                            "fail": fail_count,
                            "idx": r["idx"] + 1,
                            "title": r["title"],
                            "item_ok": r["ok"],
                            "error": r.get("error", ""),
                        }) + "\n"
                    except Exception as e:
                        fail_count += 1
                        logger.error(f"[WooFeedGen] Future error: {e}")

                yield json.dumps({
                    "type": "done",
                    "ok": ok_count,
                    "fail": fail_count,
                    "total": total,
                }) + "\n"

        try:
            return Response(
                generate(),
                mimetype="application/x-ndjson",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache",
                },
            )
        except Exception as e:
            logger.error(f"[WooFeedGen] Stream setup error: {e}")
            return jsonify({"code": 500, "message": str(e)[:100]}), 500

    @app.route("/api/shai-pin/woocommerce/sync-to-site", methods=["DELETE"])
    @jwt_required()
    def woocommerce_clean_from_site():
        """Delete all products from site. WordPress: via WP API. Static: delete from DB + regenerate."""
        data = request.get_json(silent=True) or {}
        site_id = data.get("site_id")
        if not site_id:
            return jsonify({"code": 400, "message": "请选择目标站点"}), 400

        site = get_site(site_id)
        if not site:
            return jsonify({"code": 404, "message": "站点不存在"}), 404

        # Static site: delete from local DB
        if site.get("site_type") == "static":
            conn = get_db()
            try:
                cur = conn.execute("DELETE FROM static_site_products WHERE site_id = ?", (site_id,))
                deleted = cur.rowcount
                conn.commit()
            finally:
                conn.close()
            try:
                _regenerate_static_site_html(None, site_id)
            except Exception:
                pass
            return jsonify({"code": 200, "data": {"deleted": deleted, "failed": 0}})

        # WordPress site (legacy)
        try:
            wp = WordPressAdminSession(site["url"], site["admin_name"], site["admin_password"])
            result = wp.delete_all_woocommerce_products()
            logger.info(f"[WooSync] Cleaned site {site_id}: {result}")
            return jsonify({
                "code": 200,
                "data": {
                    "deleted": result.get("deleted", 0),
                    "failed": result.get("failed", 0),
                },
            })
        except Exception as e:
            logger.error(f"[WooSync] Clean error: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500


    def _fetch_amazon_product_detail(crawlbase_keys, product_url):
        """Fetch Amazon product detail page via Crawlbase and extract all available fields."""
        from urllib.parse import quote, urlparse
        import time as _time
        from services.api_key_rotator import rotate_crawlbase

        CRAWLBASE_API = "https://api.crawlbase.com/"

        # Defensive: if product_url is doubled (e.g. "https://www.amazon.comhttps://www.amazon.com/dp/..."),
        # extract just the last valid URL portion. This is a safety net; the root cause is fixed in
        # _extract_amazon_search_products.
        if product_url and "https://" in product_url:
            # Find the last "https://" — that marks the actual product URL
            parts = product_url.rsplit("https://", 1)
            if len(parts) == 2 and parts[0]:  # there was a doubled prefix
                logger.warning(f"[AmazonConvert] Doubled URL detected, fixing: {product_url[:120]}")
                product_url = "https://" + parts[1]
                logger.warning(f"[AmazonConvert] Fixed to: {product_url[:120]}")

        encoded_url = quote(product_url, safe="")

        t0 = _time.time()
        resp = rotate_crawlbase(
            lambda k: http_requests.get(f"{CRAWLBASE_API}?token={k}&url={encoded_url}&autoparse=true", timeout=90),
            crawlbase_keys,
        )
        elapsed = _time.time() - t0
        logger.info(f"[AmazonConvert] Crawlbase response HTTP {resp.status_code} in {elapsed:.1f}s")

        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")

        raw = resp.json()
        if not isinstance(raw, dict):
            raise Exception("Response not JSON dict")

        pc_status = raw.get("pc_status")
        if pc_status and pc_status >= 400:
            raise Exception(f"pc_status={pc_status}")

        body = raw.get("body") if isinstance(raw.get("body"), dict) else raw
        if not isinstance(body, dict):
            raise Exception("Body not a dict")

        # Log available keys for debugging
        logger.info(f"[AmazonConvert] Raw body top-level keys: {list(body.keys())}")
        logger.info(f"[AmazonConvert] Raw body sample: {json.dumps({k: str(v)[:100] for k, v in body.items()}, ensure_ascii=False)}")

        # Extract all available fields from Amazon autoparse
        title = body.get("title") or body.get("name") or body.get("productName") or ""
        price = (
            body.get("price") or body.get("salePrice") or body.get("currentPrice")
            or body.get("rawPrice") or body.get("minPrice") or body.get("buyingPrice") or ""
        )
        currency = body.get("currency") or body.get("priceCurrency") or "USD"
        brand = body.get("brand") or body.get("manufacturer") or body.get("vendor") or ""
        item_id = (
            body.get("asin") or body.get("itemId") or body.get("productId")
            or body.get("sku") or body.get("mpn") or ""
        )
        ratings = str(body.get("rating") or body.get("ratings") or body.get("ratingScore") or "")
        reviews_count = int(body.get("reviewsCount") or body.get("ratingsCount") or body.get("reviewCount") or 0)
        description = body.get("description") or body.get("longDescription") or body.get("productDescription") or ""
        if not description:
            # Try bullet points as description
            bullets = body.get("features") or body.get("bulletPoints") or body.get("keyFeatures") or []
            if bullets:
                description = "\n".join(bullets if isinstance(bullets, list) else [bullets])

        images = body.get("images") or body.get("imageUrls") or body.get("gallery") or []
        if isinstance(images, str):
            images = [images]
        if not isinstance(images, list):
            images = []

        features = body.get("features") or body.get("bulletPoints") or body.get("keyFeatures") or []
        if isinstance(features, str):
            features = [features]
        if not isinstance(features, list):
            features = []
        # Normalize to strings: Crawlbase may return [{"text": "..."}, ...] or ["...", ...]
        features = [
            (f.get("text") or f.get("value") or str(f)).strip()
            if isinstance(f, dict) else str(f).strip()
            for f in features
            if f
        ]

        breadcrumbs = body.get("breadCrumbs") or body.get("breadcrumbs") or body.get("categoryPath") or []
        if isinstance(breadcrumbs, str):
            breadcrumbs = [breadcrumbs]
        if not isinstance(breadcrumbs, list):
            breadcrumbs = []
        # Normalize to strings: Crawlbase may return [{"name": "Home", "url": "..."}, ...]
        breadcrumbs = [
            (b.get("name") or b.get("title") or b.get("text") or str(b)).strip()
            if isinstance(b, dict) else str(b).strip()
            for b in breadcrumbs
            if b
        ]

        thumbnail = body.get("thumbnail") or body.get("mainImage") or body.get("primaryImage") or ""
        if not thumbnail and images:
            thumbnail = images[0]

        # Additional fields — extract everything Crawlbase Amazon autoparse can provide
        availability = body.get("availability") or body.get("stockStatus") or ""
        specifications = body.get("specifications") or body.get("specs") or {}
        answered_questions = int(body.get("answeredQuestions") or body.get("answeredQuestionsCount") or 0)
        original_price = body.get("originalPrice") or body.get("listPrice") or ""
        discount = body.get("discount") or body.get("percentOff") or body.get("savingsAmount") or ""
        best_seller_rank = body.get("bestSellerRank") or body.get("salesRank") or ""
        seller_name = body.get("sellerName") or body.get("seller") or body.get("merchantName") or ""
        seller_url = body.get("sellerUrl") or body.get("merchantUrl") or ""
        seller_rating = body.get("sellerRating") or ""
        is_prime = body.get("isPrime") or body.get("prime") or False
        condition = body.get("condition") or body.get("itemCondition") or "New"
        delivery_info = body.get("deliveryInfo") or body.get("delivery") or ""
        coupon_text = body.get("couponText") or body.get("coupon") or ""
        dimensions = body.get("dimensions") or body.get("productDimensions") or ""
        weight = body.get("weight") or body.get("itemWeight") or ""
        variants = (
            body.get("variants") or body.get("variations")
            or body.get("asinVariationValues") or []
        )
        estimated_sales = body.get("estimatedSales") or body.get("monthlySales") or ""

        # Pack all supplementary fields into extra_data dict
        extra_data = {
            "availability": str(availability).strip() if availability else "",
            "originalPrice": str(original_price).strip() if original_price else "",
            "discount": str(discount).strip() if discount else "",
            "bestSellerRank": str(best_seller_rank).strip() if best_seller_rank else "",
            "sellerName": str(seller_name).strip() if seller_name else "",
            "sellerUrl": str(seller_url).strip() if seller_url else "",
            "sellerRating": str(seller_rating).strip() if seller_rating else "",
            "isPrime": bool(is_prime) if is_prime else False,
            "condition": str(condition).strip() if condition else "New",
            "deliveryInfo": str(delivery_info).strip() if delivery_info else "",
            "couponText": str(coupon_text).strip() if coupon_text else "",
            "dimensions": str(dimensions).strip() if dimensions else "",
            "weight": str(weight).strip() if weight else "",
            "answeredQuestions": answered_questions,
            "specifications": specifications if isinstance(specifications, dict) else {},
            "variants": variants if isinstance(variants, list) else [],
            "estimatedSales": str(estimated_sales).strip() if estimated_sales else "",
        }

        logger.info(
            f"[AmazonConvert] Extracted: title='{title[:50]}', "
            f"price='{price}', originalPrice='{original_price}', brand='{brand}', item_id='{item_id}', "
            f"ratings='{ratings}', reviews={reviews_count}, "
            f"images={len(images)}, features={len(features)}, "
            f"seller='{seller_name}', prime={is_prime}, availability='{availability}', "
            f"bsr='{best_seller_rank}', condition='{condition}'"
        )

        return {
            "title": str(title).strip(),
            "price": str(price).strip() if price else "",
            "currency": str(currency).strip() if currency else "USD",
            "brand": str(brand).strip(),
            "item_id": str(item_id).strip(),
            "ratings": str(ratings).strip() if ratings else "",
            "reviews_count": reviews_count,
            "description": str(description).strip() if description else "",
            "images": images,
            "features": features,
            "breadcrumbs": breadcrumbs,
            "thumbnail": str(thumbnail).strip() if thumbnail else "",
            "source_url": product_url,
            "extra_data": extra_data,
        }


    def _extract_amazon_search_products(body, search_query=""):
        """Parse Crawlbase autoparse body from Amazon search results page."""
        products = []

        # Amazon autoparse may return products array or searchResults structured data
        raw_products = body.get("products") or body.get("searchResults") or []

        # Some Crawlbase responses wrap items in a list of cards
        if not raw_products:
            # Try common Crawlbase autoparse keys for Amazon search
            for key in ("results", "items", "data"):
                val = body.get(key)
                if isinstance(val, list) and val:
                    raw_products = val
                    break

        for item in raw_products:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name") or item.get("productName") or ""
            if not title:
                continue
            price = (
                item.get("price") or item.get("salePrice") or item.get("currentPrice")
                or item.get("rawPrice") or item.get("minPrice") or item.get("buyingPrice") or ""
            )

            # Debug: log all keys that could be the product URL
            raw_link = item.get("link")
            raw_url = item.get("url")
            raw_purl = item.get("productUrl")
            logger.info(
                f"[AmazonExtract] link=({type(raw_link).__name__}) {str(raw_link)[:120]!r} | "
                f"url=({type(raw_url).__name__}) {str(raw_url)[:120]!r} | "
                f"productUrl=({type(raw_purl).__name__}) {str(raw_purl)[:120]!r}"
            )
            link = raw_link or raw_url or raw_purl or ""
            if link and not link.startswith("http"):
                link = f"https://www.amazon.com{link}" if link.startswith("/") else f"https://www.amazon.com/{link}"
            # Defensive: fix doubled URLs (e.g. Crawlbase returns https://www.amazon.comhttps://www.amazon.com/dp/...)
            if link and link.count("https://") > 1:
                logger.warning(f"[AmazonExtract] Doubled URL detected, fixing: {link[:150]}")
                # Extract the last valid full URL from the string
                parts = link.rsplit("https://", 1)
                if len(parts) == 2:
                    link = "https://" + parts[1]
                    logger.warning(f"[AmazonExtract] Fixed to: {link[:150]}")
            image = item.get("image") or item.get("thumbnail") or item.get("imageUrl") or ""
            ratings = item.get("ratings") or item.get("rating") or item.get("ratingScore") or ""
            reviews = item.get("reviewsCount") or item.get("reviews") or item.get("reviewCount") or 0
            asin = item.get("asin") or item.get("itemId") or item.get("id") or ""
            brand = item.get("brand") or item.get("brandName") or item.get("manufacturer") or ""
            breadcrumbs = item.get("breadcrumbs") or item.get("category") or item.get("categoryPath") or ""
            if isinstance(breadcrumbs, list):
                breadcrumbs = " > ".join(str(b) for b in breadcrumbs if b)
            features = item.get("features") or item.get("bulletPoints") or item.get("highlights") or ""
            if isinstance(features, list):
                features = "||".join(str(f) for f in features if f)
            original_price = item.get("originalPrice") or item.get("listPrice") or item.get("strikethroughPrice") or ""
            is_prime = item.get("isPrime") or item.get("prime") or False
            delivery = item.get("delivery") or item.get("deliveryInfo") or item.get("shipping") or ""

            products.append({
                "product_name": str(title).strip(),
                "price": str(price).strip() if price else "",
                "source_url": str(link).strip() if link else "",
                "thumbnail": str(image).strip() if image else "",
                "rating_score": _safe_float(ratings) if ratings else 0,
                "review_count": int(reviews) if reviews else 0,
                "search_query": search_query,
                "asin": str(asin).strip() if asin else "",
                "brand": str(brand).strip() if brand else "",
                "breadcrumbs": str(breadcrumbs).strip() if breadcrumbs else "",
                "features": str(features).strip() if features else "",
                "original_price": str(original_price).strip() if original_price else "",
                "is_prime": bool(is_prime),
                "delivery": str(delivery).strip() if delivery else "",
            })

        return products


    def _safe_float(val):
        """Safely parse a float value."""
        try:
            return float(str(val).replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            return 0.0


    # ---- Feed Stats Dashboard ----
    @app.route("/api/feed/stats", methods=["GET"])
    @jwt_required()
    def feed_stats_route():
        try:
            stats = get_feed_stats()
            return jsonify({"code": 200, "data": stats})
        except Exception as e:
            logger.error(f"Feed stats failed: {e}")
            return jsonify({"code": 500, "message": f"获取统计失败: {str(e)[:100]}"}), 500

    # ---- WooCommerce Sales Stats ----

    @app.route("/api/stats/woocommerce", methods=["GET"])
    @jwt_required()
    def wc_stats_route():
        """Aggregate WooCommerce sales data across all visible sites."""
        period = request.args.get("period", "month")
        date_min = request.args.get("date_min", None)
        date_max = request.args.get("date_max", None)

        user_id = get_current_user_id()
        user = get_user_by_id(user_id)
        if user and user.get("role") == "admin":
            sites = list_sites()
        else:
            sites = list_sites(user_id=user_id)

        summary = {
            "total_sales": 0.0, "net_sales": 0.0, "total_orders": 0,
            "average_sales": 0.0, "total_items": 0,
            "active_sites": 0, "total_sites": len(sites),
        }
        site_results = []

        for site in sites:
            sid = site["id"]
            site_entry = {
                "id": sid, "site_name": site["site_name"], "url": site["url"],
                "total_sales": 0.0, "net_sales": 0.0, "total_orders": 0,
                "average_sales": 0.0, "status": "ok",
            }
            try:
                wp = WordPressAdminSession(
                    site_url=site["url"] or f"http://{site['site_name']}",
                    username=site.get("admin_name", "admin"),
                    password=site.get("admin_password", ""),
                )
                if not wp.login():
                    site_entry["status"] = "unreachable"
                    site_results.append(site_entry)
                    continue

                if not wp.is_woocommerce_active():
                    site_entry["status"] = "no_woocommerce"
                    site_results.append(site_entry)
                    continue

                report = wp.get_wc_sales_report(period=period, date_min=date_min, date_max=date_max)
                if not report:
                    site_entry["status"] = "no_data"
                    site_results.append(site_entry)
                    continue

                site_entry["total_sales"] = float(report.get("total_sales", 0) or 0)
                site_entry["net_sales"] = float(report.get("net_sales", 0) or 0)
                site_entry["total_orders"] = int(report.get("total_orders", 0) or 0)
                site_entry["average_sales"] = float(report.get("average_sales", 0) or 0)

                site_results.append(site_entry)

                summary["total_sales"] += site_entry["total_sales"]
                summary["net_sales"] += site_entry["net_sales"]
                summary["total_orders"] += site_entry["total_orders"]
                summary["total_items"] += int(report.get("total_items", 0) or 0)
                summary["active_sites"] += 1

            except Exception as e:
                logger.warning("WC stats for site %s failed: %s", site.get("site_name"), e)
                site_entry["status"] = "unreachable"
                site_results.append(site_entry)

        if summary["active_sites"] > 0:
            summary["average_sales"] = round(
                summary["net_sales"] / summary["active_sites"], 2
            )

        return jsonify({"code": 200, "data": {
            "period": period,
            "summary": summary,
            "sites": site_results,
        }})

    # ---- Feed Products (Google Merchant Center) ----

    @app.route("/api/sites/<int:site_id>/feed-products", methods=["GET"])
    @jwt_required()
    def list_feed_products_route(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            products = list_feed_products(site_id)
            return jsonify({"code": 200, "data": products})
        except Exception as e:
            logger.error(f"Feed products list failed: {e}")
            return jsonify({"code": 500, "message": f"获取商品列表失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/feed-products", methods=["POST"])
    @jwt_required()
    def create_feed_product_route(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            data = request.get_json(silent=True) or {}
            data["site_id"] = site_id
            if not data.get("title"):
                return jsonify({"code": 400, "message": "商品标题不能为空"}), 400
            product = create_feed_product(data)
            return jsonify({"code": 200, "data": product})
        except Exception as e:
            logger.error(f"Feed product create failed: {e}")
            return jsonify({"code": 500, "message": f"创建商品失败: {str(e)[:100]}"}), 500

    @app.route("/api/feed-products/<int:product_id>", methods=["PUT"])
    @jwt_required()
    def update_feed_product_route(product_id):
        try:
            product = get_feed_product(product_id)
            if not product:
                return jsonify({"code": 404, "message": "商品不存在"}), 404
            data = request.get_json(silent=True) or {}
            updated = update_feed_product(product_id, data)
            return jsonify({"code": 200, "data": updated})
        except Exception as e:
            logger.error(f"Feed product update failed: {e}")
            return jsonify({"code": 500, "message": f"更新商品失败: {str(e)[:100]}"}), 500

    @app.route("/api/feed-products/<int:product_id>", methods=["DELETE"])
    @jwt_required()
    def delete_feed_product_route(product_id):
        try:
            product = get_feed_product(product_id)
            if not product:
                return jsonify({"code": 404, "message": "商品不存在"}), 404
            delete_feed_product(product_id)
            return jsonify({"code": 200, "message": "商品已删除"})
        except Exception as e:
            logger.error(f"Feed product delete failed: {e}")
            return jsonify({"code": 500, "message": f"删除商品失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/feed-products/sample", methods=["POST"])
    @jwt_required()
    def create_sample_feed_products_route(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            domain = site.get("site_name", "")
            products = create_sample_feed_products(site_id, domain)
            return jsonify({"code": 200, "data": products, "message": f"已导入 {len(products)} 个示例商品"})
        except Exception as e:
            logger.error(f"Sample feed products create failed: {e}")
            return jsonify({"code": 500, "message": f"导入示例失败: {str(e)[:100]}"}), 500

    @app.route("/api/sites/<int:site_id>/feed-products/export", methods=["GET"])
    @jwt_required()
    def export_feed_products_route(site_id):
        try:
            site = get_site(site_id)
            if not site:
                return jsonify({"code": 404, "message": "站点不存在"}), 404
            products = list_feed_products(site_id)
            domain = site.get("site_name", "example.com")

            def cdata(val):
                return f"<![CDATA[{val}]]>"

            items = []
            for p in products:
                items.append(f"""  <item>
    <g:id>{p['id']}</g:id>
    <title>{cdata(p['title'])}</title>
    <description>{cdata(p['description'])}</description>
    <link>{cdata(p['link'] or f'https://{domain}')}</link>
    <g:image_link>{cdata(p['image_url'])}</g:image_link>
    <g:price>{p['price']}</g:price>
    <g:availability>{p['availability']}</g:availability>
    <g:condition>{p['condition']}</g:condition>
    <g:brand>{cdata(p['brand'])}</g:brand>
    <g:gtin>{p['gtin']}</g:gtin>
    <g:mpn>{p['mpn']}</g:mpn>
    <g:google_product_category>{cdata(p['google_product_category'])}</g:google_product_category>
    <g:product_type>{cdata(p['product_type'])}</g:product_type>
    <g:shipping>
      <g:country>US</g:country>
      <g:service>Standard</g:service>
      <g:price>{p.get('shipping', '0.00 USD')}</g:price>
    </g:shipping>
  </item>""")

            xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">
<channel>
  <title>{cdata(site.get('tag') or domain)}</title>
  <link>{cdata(f'https://{domain}')}</link>
  <description>{cdata(f'Product feed for {domain}')}</description>
{chr(10).join(items)}
</channel>
</rss>"""

            from flask import Response
            return Response(xml, mimetype="application/xml",
                            headers={"Content-Disposition": f"attachment; filename=feed_{site_id}.xml"})
        except Exception as e:
            logger.error(f"Feed export failed: {e}")
            return jsonify({"code": 500, "message": f"导出失败: {str(e)[:100]}"}), 500

    # ---- Brand Kit (品牌套件) ----

    def _generate_business_info(brand_name: str, industry: str = "") -> dict:
        """Generate realistic US business info from brand name.

        Returns a dict with: company_name, address, city, state, postcode, phone, email, country_state
        """
        import random

        _US_CITIES = [
            # (city, state, state_code, zip_code, area_code)
            ("New York", "New York", "NY", "10001", "212"),
            ("Los Angeles", "California", "CA", "90001", "213"),
            ("Chicago", "Illinois", "IL", "60601", "312"),
            ("Houston", "Texas", "TX", "77001", "713"),
            ("Phoenix", "Arizona", "AZ", "85001", "602"),
            ("Philadelphia", "Pennsylvania", "PA", "19101", "215"),
            ("San Antonio", "Texas", "TX", "78201", "210"),
            ("San Diego", "California", "CA", "92101", "619"),
            ("Dallas", "Texas", "TX", "75201", "214"),
            ("Austin", "Texas", "TX", "73301", "512"),
            ("Portland", "Oregon", "OR", "97201", "503"),
            ("Seattle", "Washington", "WA", "98101", "206"),
            ("Denver", "Colorado", "CO", "80201", "303"),
            ("Nashville", "Tennessee", "TN", "37201", "615"),
            ("Atlanta", "Georgia", "GA", "30301", "404"),
            ("Miami", "Florida", "FL", "33101", "305"),
            ("Detroit", "Michigan", "MI", "48201", "313"),
            ("Minneapolis", "Minnesota", "MN", "55401", "612"),
            ("Tampa", "Florida", "FL", "33601", "813"),
            ("Orlando", "Florida", "FL", "32801", "407"),
            ("Charlotte", "North Carolina", "NC", "28201", "704"),
            ("Raleigh", "North Carolina", "NC", "27601", "919"),
            ("Indianapolis", "Indiana", "IN", "46201", "317"),
            ("Columbus", "Ohio", "OH", "43201", "614"),
            ("Las Vegas", "Nevada", "NV", "89101", "702"),
            ("Salt Lake City", "Utah", "UT", "84101", "801"),
            ("Boston", "Massachusetts", "MA", "02101", "617"),
            ("St. Louis", "Missouri", "MO", "63101", "314"),
            ("Pittsburgh", "Pennsylvania", "PA", "15201", "412"),
            ("Kansas City", "Missouri", "MO", "64101", "816"),
        ]

        _STREETS = [
            "Main St", "Oak Ave", "Commerce Blvd", "Park Rd", "Broadway",
            "Market St", "Industrial Pkwy", "Willow Dr", "Cedar Ln", "Elm St",
            "First Ave", "Washington Blvd", "Lincoln Rd", "Highland Dr", "River Rd",
            "Lakeview Dr", "Sunset Blvd", "Valley Rd", "Forest Ave", "Springfield Dr",
        ]

        city, state, state_code, postcode, area_code = random.choice(_US_CITIES)
        street_num = random.randint(100, 9999)
        street = random.choice(_STREETS)

        # Generate company name from brand
        suffixes = ["LLC", "Inc.", "Corp.", "Ltd.", "Group"]
        name_lower = brand_name.lower()
        company_name = brand_name
        if not any(kw in name_lower for kw in ["llc", "inc", "ltd", "corp", "co.", "group", "store", "shop"]):
            company_name = f"{brand_name} {random.choice(suffixes)}"

        # Phone: matched to city's real area code
        phone = f"+1-{area_code}-{random.randint(200,999)}-{random.randint(1000,9999)}"

        return {
            "company_name": company_name,
            "address": f"{street_num} {street}",
            "city": city,
            "state": state,
            "state_code": state_code,
            "postcode": postcode,
            "country": "US",
            "country_state": f"US:{state_code}",
            "phone": phone,
            "email": "",  # filled from Google account later
        }

    @app.route("/api/brand-kits", methods=["GET"])
    @jwt_required()
    def list_brand_kits_route():
        """List all brand kits."""
        try:
            claims = get_jwt()
            user_id = None if claims.get("role") == "admin" else claims.get("user_id")
            kits = list_brand_kits(user_id=user_id)
            return jsonify({"code": 200, "data": kits})
        except Exception as e:
            logger.error(f"list_brand_kits: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/brand-kits", methods=["POST"])
    @jwt_required()
    def create_brand_kit_route():
        """Create a new brand kit."""
        try:
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"code": 400, "message": "品牌套件名称不能为空"}), 400
            brand_name = (data.get("brand_name") or "").strip() or name
            industry = (data.get("industry") or "").strip()
            business_info = data.get("business_info") or _generate_business_info(brand_name, industry)
            if isinstance(business_info, str):
                business_info = json.loads(business_info) if business_info else {}
            # Auto-fill woo_config from business_info if no woo_config provided
            woo_config = data.get("woo_config", {})
            if not woo_config and business_info:
                woo_config = {
                    "address": business_info.get("address", ""),
                    "city": business_info.get("city", ""),
                    "country_state": business_info.get("country_state", ""),
                    "postcode": business_info.get("postcode", ""),
                    "allowed_countries": "US",
                }
            # Auto-generate default tax config (US standard rate)
            tax_config = data.get("tax_config", {})
            if not tax_config:
                tax_config = {
                    "tax_enabled": True,
                    "prices_include_tax": False,
                    "tax_rates": [{
                        "name": f"{business_info.get('state_code', 'US')} State Tax",
                        "rate": "8.25",
                        "country": "US",
                        "state": business_info.get("state_code", ""),
                        "shipping": True,
                        "priority": 1,
                    }],
                }
            # Auto-generate default free shipping config
            shipping_config = data.get("shipping_config", {})
            if not shipping_config:
                shipping_config = {
                    "zone_name": "Free Shipping",
                    "country": "US",
                }
            # Auto-generate footer config from business info if not provided
            footer_config = data.get("footer_config", {})
            if not footer_config and business_info:
                footer_config = {
                    "address": f"{business_info.get('address', '')}, {business_info.get('city', '')}, {business_info.get('state_code', '')} {business_info.get('postcode', '')}",
                    "phone": business_info.get("phone", ""),
                    "email": business_info.get("email", ""),
                }
            proxy_id_in = data.get("proxy_id") or None
            ds = {}
            style_recipe = (data.get("style_recipe") or "").strip()
            if not style_recipe:
                import random
                from garden_recipes import STYLE_RECIPES
                style_recipe = random.choice(list(STYLE_RECIPES.keys()))
            ds = {"style_recipe": style_recipe}
            ga_id_in = data.get("google_account_id") or None
            logger.info(f"[create_brand_kit] proxy_id={proxy_id_in!r} google_account_id={ga_id_in!r} data_keys={list(data.keys())}")
            kit = create_brand_kit({
                "name": name,
                "brand_name": brand_name,
                "description": (data.get("description") or "").strip(),
                "industry": industry,
                "proxy": (data.get("proxy") or "").strip(),
                "proxy_id": proxy_id_in,
                "google_account_id": ga_id_in,
                "woo_config": woo_config,
                "footer_config": footer_config,
                "business_info": business_info,
                "tax_config": tax_config,
                "shipping_config": shipping_config,
                "status": "draft",
                "design_system": ds,
                "created_by": get_current_user_id(),
            })
            return jsonify({"code": 200, "data": kit, "message": "品牌套件已创建"})
        except Exception as e:
            logger.error(f"create_brand_kit: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/brand-kits/<int:kit_id>", methods=["GET"])
    @jwt_required()
    def get_brand_kit_route(kit_id):
        """Get a single brand kit."""
        try:
            kit = get_brand_kit(kit_id)
            if not kit:
                return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
            return jsonify({"code": 200, "data": kit})
        except Exception as e:
            logger.error(f"get_brand_kit: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/brand-kits/<int:kit_id>", methods=["PUT"])
    @jwt_required()
    def update_brand_kit_route(kit_id):
        """Update brand kit metadata."""
        try:
            kit = get_brand_kit(kit_id)
            if not kit:
                return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
            data = request.get_json(silent=True) or {}
            sr = (data.pop("style_recipe", "") or "").strip()
            if sr:
                eds = kit.get("design_system", {}) or {}
                if isinstance(eds, str):
                    try: eds = json.loads(eds)
                    except: eds = {}
                eds["style_recipe"] = sr
                data["design_system"] = eds
            updated = update_brand_kit(kit_id, data)
            return jsonify({"code": 200, "data": updated, "message": "已更新"})
        except Exception as e:
            logger.error(f"update_brand_kit: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500
        try:
            kit = get_brand_kit(kit_id)
            if not kit:
                return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
            data = request.get_json(silent=True) or {}
            updated = update_brand_kit(kit_id, data)
            return jsonify({"code": 200, "data": updated, "message": "已更新"})
        except Exception as e:
            logger.error(f"update_brand_kit: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/brand-kits/<int:kit_id>/config", methods=["PUT"])
    @jwt_required()
    def save_brand_kit_config_route(kit_id):
        try:
            kit = get_brand_kit(kit_id)
            if not kit:
                return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
            data = request.get_json(silent=True) or {}
            updates = {}
            for key in ("woo_config", "footer_config", "tax_config", "shipping_config"):
                if key in data:
                    updates[key] = data[key]
            if not updates:
                return jsonify({"code": 400, "message": "无有效数据"}), 400
            updated = update_brand_kit(kit_id, updates)
            return jsonify({"code": 200, "data": updated, "message": "配置已保存"})
        except Exception as e:
            logger.error(f"save_brand_kit_config: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/brand-kits/<int:kit_id>", methods=["DELETE"])
    @jwt_required()
    def delete_brand_kit_route(kit_id):
        """Delete brand kit and its assets.
        Query params: mode=release (default) | deprecate
        """
        try:
            kit = get_brand_kit(kit_id)
            if not kit:
                return jsonify({"code": 404, "message": "品牌套件不存在"}), 404
            mode = request.args.get("mode", "release")
            if mode not in ("release", "deprecate"):
                mode = "release"
            delete_brand_kit(kit_id, proxy_mode=mode)
            msg = "已删除（代理已弃用）" if mode == "deprecate" else "已删除（代理已释放）"
            return jsonify({"code": 200, "message": msg})
        except Exception as e:
            logger.error(f"delete_brand_kit: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/brand-kits/<int:kit_id>/generate", methods=["POST"])
    @jwt_required()
    def generate_brand_kit_route(kit_id):
        """Start AI logo generation + LogoLoom pipeline (runs in background thread)."""
        kit = get_brand_kit(kit_id)
        if not kit:
            return jsonify({"code": 404, "message": "品牌套件不存在"}), 404

        # Prevent concurrent generation
        existing = brand_kit_generation_status.get(kit_id)
        if existing and existing.get("status") == "running":
            return jsonify({"code": 409, "message": "品牌套件正在生成中，请等待完成"}), 409

        from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
        deepseek_keys = get_deepseek_keys()
        if not deepseek_keys:
            return jsonify({"code": 400, "message": "请先在系统设置中配置 DeepSeek API Key"}), 400

        update_brand_kit(kit_id, {"status": "generating", "error_message": ""})

        brand_kit_generation_status[kit_id] = {
            "status": "running",
            "message": "AI 生成中...",
            "steps": [
                {"label": "AI 生成 SVG Logo", "status": "pending", "message": ""},
                {"label": "AI 生成商家信息", "status": "pending", "message": ""},
                {"label": "SVG 文字转路径", "status": "pending", "message": ""},
                {"label": "SVG 优化", "status": "pending", "message": ""},
                {"label": "导出品牌套件", "status": "pending", "message": ""},
                {"label": "创建指纹环境", "status": "pending", "message": ""},
            ],
        }

        def _do_generate():
            with app.app_context():
                try:
                    from services.logoloom_service import LogoLoomService, LogoLoomError
                    steps = brand_kit_generation_status[kit_id]["steps"]

                    # Step 1: AI generation
                    steps[0]["status"] = "running"
                    steps[0]["message"] = "正在调用 AI..."
                    result = rotate_deepseek(
                        lambda key: LogoLoomService.generate_svg_logo(
                            brand_name=kit.get("brand_name") or kit.get("name", ""),
                            description=kit.get("description", ""),
                            industry=kit.get("industry", ""),
                            deepseek_api_key=key,
                        ),
                        deepseek_keys,
                    )
                    svg = result["svg"]
                    colors = result.get("colors", [])
                    typography = result.get("typography", {})
                    steps[0]["status"] = "done"
                    steps[0]["message"] = "SVG Logo 已生成"

                    base_dir = os.path.join(os.path.dirname(__file__), "brand-kits")
                    kit_dir = os.path.join(base_dir, str(kit_id))
                    os.makedirs(kit_dir, exist_ok=True)

                    # Save raw SVG
                    with open(os.path.join(kit_dir, "logo-raw.svg"), "w", encoding="utf-8") as f:
                        f.write(svg)

                    update_brand_kit(kit_id, {
                        "raw_svg": svg, "colors": colors, "typography": typography,
                        "directory": f"brand-kits/{kit_id}",
                    })

                    # Check if kit still exists (not deleted during generation)
                    if not get_brand_kit(kit_id):
                        return

                    # Step 2: AI generate business info
                    steps[1]["status"] = "running"
                    steps[1]["message"] = "正在生成商家信息..."
                    try:
                        brand_name = kit.get("brand_name") or kit.get("name", "")
                        industry = kit.get("industry", "")
                        biz_prompt = f"""You are a realistic business data generator. Generate a complete and realistic US business profile for a brand.

Brand name: {brand_name}
{f"Industry: {industry}" if industry else ""}

IMPORTANT requirements:
- postcode: MUST be a REAL, valid 5-digit USPS ZIP code for the chosen city
- phone: area code MUST match the city's real area code(s)
- address: MUST be a real street address in that city
- email: use "placeholder@placeholder.com" as placeholder

Respond with strict JSON only (no markdown code blocks):
{{
  "company_name": "Realistic company legal name",
  "address": "Real street address in a major US city",
  "city": "City name",
  "state": "Full state name",
  "state_code": "Two-letter state code",
  "postcode": "5-digit real ZIP code for this city",
  "phone": "+1-NPA-NXX-XXXX (area code must match the city)",
  "email": "placeholder@placeholder.com"
}}"""
                        def _biz_call(key):
                            return http_requests.post(
                                "https://api.deepseek.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                json={
                                    "model": "deepseek-chat",
                                    "messages": [
                                        {"role": "system", "content": "你是一个专业的商业数据生成助手，只返回JSON格式数据。"},
                                        {"role": "user", "content": biz_prompt},
                                    ],
                                    "temperature": 0.7, "max_tokens": 1024,
                                },
                                timeout=60,
                            )
                        biz_resp = rotate_deepseek(_biz_call, deepseek_keys)
                        biz_info = {}
                        if biz_resp.status_code == 200:
                            biz_text = biz_resp.json()["choices"][0]["message"]["content"].strip()
                            if "```" in biz_text:
                                biz_text = re.sub(r'^```(?:json)?\s*\n?', '', biz_text)
                                biz_text = re.sub(r'\n?```\s*$', '', biz_text)
                            biz_info = json.loads(biz_text)
                        # Fallback: use template if AI fails
                        if not biz_info.get("city"):
                            biz_info = _generate_business_info(brand_name, industry)
                        biz_info["country"] = "US"
                        biz_info["country_state"] = f"US:{biz_info.get('state_code', '')}"
                        # Auto-fill woo_config from business info
                        woo_config = {
                            "address": biz_info.get("address", ""),
                            "city": biz_info.get("city", ""),
                            "country_state": biz_info.get("country_state", ""),
                            "postcode": biz_info.get("postcode", ""),
                            "allowed_countries": "US",
                        }
                        tax_config = {
                            "tax_enabled": True,
                            "prices_include_tax": False,
                            "tax_rates": [{
                                "name": f"{biz_info.get('state_code', 'US')} State Tax",
                                "rate": "8.25",
                                "country": "US",
                                "state": biz_info.get("state_code", ""),
                                "shipping": True,
                                "priority": 1,
                            }],
                        }
                        footer_config = {
                            "address": f"{biz_info.get('address', '')}, {biz_info.get('city', '')}, {biz_info.get('state_code', '')} {biz_info.get('postcode', '')}",
                            "phone": biz_info.get("phone", ""),
                            "email": biz_info.get("email", ""),
                        }
                        shipping_config = {
                            "zone_name": "Free Shipping",
                            "country": "US",
                        }
                        # Override email from bound Google account
                        google_email = ""
                        try:
                            ga_id = kit.get("google_account_id")
                            if ga_id:
                                ga = get_google_account(int(ga_id))
                                if ga and ga.get("email"):
                                    google_email = ga["email"]
                                    biz_info["email"] = google_email
                                    footer_config["email"] = google_email
                                    logger.info(f"Brand kit {kit_id}: using Google email {google_email}")
                        except Exception as e_ga:
                            logger.warning(f"Brand kit {kit_id}: failed to get Google email: {e_ga}")
                        update_brand_kit(kit_id, {
                            "business_info": biz_info,
                            "woo_config": woo_config,
                            "tax_config": tax_config,
                            "footer_config": footer_config,
                            "shipping_config": shipping_config,
                        })
                        steps[1]["status"] = "done"
                        steps[1]["message"] = f"{biz_info.get('city', '')}, {biz_info.get('state_code', '')}"
                    except Exception as e:
                        logger.warning("AI business info generation failed: %s", e)
                        steps[1]["status"] = "done"
                        steps[1]["message"] = f"模板生成（AI失败: {str(e)[:30]}）"
                        # Fallback to template
                        biz_info = _generate_business_info(brand_name, industry)
                        update_brand_kit(kit_id, {
                            "business_info": biz_info,
                            "woo_config": {
                                "address": biz_info.get("address", ""),
                                "city": biz_info.get("city", ""),
                                "country_state": biz_info.get("country_state", ""),
                                "postcode": biz_info.get("postcode", ""),
                                "allowed_countries": "US",
                            },
                            "tax_config": {
                                "tax_enabled": True,
                                "prices_include_tax": False,
                                "tax_rates": [{
                                    "name": f"{biz_info.get('state_code', 'US')} State Tax",
                                    "rate": "8.25", "country": "US",
                                    "state": biz_info.get("state_code", ""),
                                    "shipping": True, "priority": 1,
                                }],
                            },
                            "footer_config": {
                                "address": f"{biz_info.get('address', '')}, {biz_info.get('city', '')}, {biz_info.get('state_code', '')} {biz_info.get('postcode', '')}",
                                "phone": biz_info.get("phone", ""),
                                "email": biz_info.get("email", ""),
                            },
                            "shipping_config": {
                                "zone_name": "Free Shipping",
                                "country": "US",
                            },
                        })

                    # Step 2.5: AI generate design_system
                    try:
                        from static_store_engine import DEFAULT_DESIGN
                        design_schema = json.dumps(DEFAULT_DESIGN, ensure_ascii=False)
                        brand_name = kit.get("brand_name") or kit.get("name", "")
                        industry = kit.get("industry", "")
                        ds_prompt = f"""You are an e-commerce design expert. Output strict JSON only.

Design a unique e-commerce storefront for brand "{brand_name}"
in the "{industry}" industry.

Primary color: {colors[0] if colors else "#e07b5a"}
Accent color: {colors[1] if len(colors) > 1 else "#2d9cdb"}

IMPORTANT: Use DIVERSE color palettes — do NOT always default to blue/dark themes.
Consider: warm earth tones, vibrant jewel tones, pastel minimalist, bold neon, elegant monochrome, nature-inspired greens, sunset gradients, ocean blues, luxury gold/black. Rotate through different color families for variety.

Output ONLY this JSON — different creative choices for each field:
{design_schema}

Make UNIQUE decisions — different hero type, card style, layout from typical defaults."""

                        def _ds_call(key):
                            return http_requests.post(
                                "https://api.deepseek.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                json={
                                    "model": "deepseek-chat",
                                    "messages": [
                                        {"role": "system", "content": "You are an e-commerce design expert. Output strict JSON only, no markdown."},
                                        {"role": "user", "content": ds_prompt},
                                    ],
                                    "temperature": 0.9, "max_tokens": 1024,
                                },
                                timeout=45,
                            )
                        ds_resp = rotate_deepseek(_ds_call, deepseek_keys)
                        design_system = DEFAULT_DESIGN
                        if ds_resp.status_code == 200:
                            ds_text = ds_resp.json()["choices"][0]["message"]["content"].strip()
                            if "```" in ds_text:
                                ds_text = re.sub(r'^```(?:json)?\s*\n?', '', ds_text)
                                ds_text = re.sub(r'\n?```\s*$', '', ds_text)
                            try:
                                design_system = json.loads(ds_text)
                            except json.JSONDecodeError:
                                logger.warning("AI design_system parse failed, using default")
                        update_brand_kit(kit_id, {"design_system": design_system})
                        logger.info("design_system generated for brand kit %s", kit_id)
                    except Exception as e:
                        logger.warning("AI design_system generation failed, using default: %s", e)
                        update_brand_kit(kit_id, {"design_system": DEFAULT_DESIGN})

                    # Step 3: Text to path (safety net for any remaining <text> elements)
                    steps[2]["status"] = "running"
                    steps[2]["message"] = "正在转换文字为路径..."
                    try:
                        svg = LogoLoomService.text_to_path(svg)
                        steps[2]["status"] = "done"
                        steps[2]["message"] = "文字已转为路径"
                    except Exception:
                        steps[2]["status"] = "done"
                        steps[2]["message"] = "无需转换（无文字元素）"

                    # Step 4: Optimize SVG
                    steps[3]["status"] = "running"
                    steps[3]["message"] = "正在优化 SVG..."
                    optimized = LogoLoomService.optimize_svg(svg)
                    with open(os.path.join(kit_dir, "logo.svg"), "w", encoding="utf-8") as f:
                        f.write(optimized)
                    steps[3]["status"] = "done"
                    update_brand_kit(kit_id, {"processed_svg": optimized})

                    # Step 5: Export brand kit
                    steps[4]["status"] = "running"
                    steps[4]["message"] = "正在导出 PNG/ICO/WebP..."
                    export_result = LogoLoomService.export_brand_kit(
                        svg_content=optimized, output_dir=kit_dir,
                        name=kit.get("name", "brand"),
                    )
                    steps[4]["status"] = "done"

                    update_brand_kit(kit_id, {
                        "status": "ready",
                        "png_256": export_result.get("png_256", ""),
                        "png_512": export_result.get("png_512", ""),
                        "png_1024": export_result.get("png_1024", ""),
                        "ico": export_result.get("ico", ""),
                        "webp": export_result.get("webp", ""),
                        "og_image": export_result.get("og_image", ""),
                        "brand_md": export_result.get("brand_md", ""),
                    })

                    # Step 6: Create CloakBrowser fingerprint profile
                    steps[5]["status"] = "running"
                    steps[5]["message"] = "正在创建 CloakBrowser 指纹环境..."
                    try:
                        from services.mc_auto_register import create_profile as cb_create_profile
                        profile_name = re.sub(r'[^a-zA-Z0-9\-_]', '-', brand_name.lower())[:30] + f"-bk{kit_id}"
                        country = biz_info.get("state_code", "US") if isinstance(biz_info, dict) else "US"
                        if not country or len(str(country)) != 2:
                            country = "US"
                        proxy = (kit.get("proxy") or "").strip()
                        try:
                            cb_create_profile(name=profile_name, country=str(country), proxy=proxy)
                            steps[5]["message"] = f"Profile: {profile_name}" + (f" (proxy)" if proxy else "")
                        except FileExistsError:
                            logger.info("Brand kit %s: profile '%s' already exists, reusing", kit_id, profile_name)
                            steps[5]["message"] = f"Profile: {profile_name} (已存在，复用)"
                        update_brand_kit(kit_id, {"cloakbrowser_profile_name": profile_name})
                        steps[5]["status"] = "done"
                    except Exception as e:
                        logger.warning("Brand kit %s: fingerprint profile creation failed: %s", kit_id, e)
                        steps[5]["status"] = "done"
                        steps[5]["message"] = f"已跳过 ({str(e)[:40]})"

                    brand_kit_generation_status[kit_id] = {
                        "status": "success", "message": "品牌套件已生成", "steps": steps,
                    }

                except Exception as e:
                    logger.error(f"Generate brand kit {kit_id}: {traceback.format_exc()}")
                    try:
                        update_brand_kit(kit_id, {"status": "failed", "error_message": str(e)[:500]})
                    except Exception:
                        pass
                    brand_kit_generation_status[kit_id] = {
                        "status": "failed", "message": str(e)[:200],
                        "steps": brand_kit_generation_status.get(kit_id, {}).get("steps", []),
                    }

        threading.Thread(target=_do_generate, daemon=True).start()
        return jsonify({"code": 200, "message": "品牌套件生成已启动", "data": {"kit_id": kit_id}})

    @app.route("/api/brand-kits/<int:kit_id>/status", methods=["GET"])
    @jwt_required()
    def get_brand_kit_generation_status_route(kit_id):
        """Poll brand kit generation progress."""
        status = brand_kit_generation_status.get(kit_id)
        if status:
            return jsonify({"code": 200, "data": status})
        # Fall back to database status (cross-gunicorn-worker support)
        kit = get_brand_kit(kit_id)
        if not kit:
            return jsonify({"code": 200, "data": {"status": "not_found"}})
        if kit.get("status") == "generating":
            return jsonify({"code": 200, "data": {
                "status": "running",
                "steps": [
                    {"label": "AI 生成 SVG Logo", "status": "done" if kit.get("raw_svg") else "pending", "message": ""},
                    {"label": "SVG 文字转路径", "status": "done" if kit.get("processed_svg") else "pending", "message": ""},
                    {"label": "SVG 优化", "status": "pending", "message": ""},
                    {"label": "导出品牌套件", "status": "done" if kit.get("png_256") else "pending", "message": ""},
                ],
            }})
        if kit.get("status") == "ready":
            return jsonify({"code": 200, "data": {"status": "ready", "steps": [
                {"label": "AI 生成 SVG Logo", "status": "done", "message": ""},
                {"label": "SVG 文字转路径", "status": "done", "message": ""},
                {"label": "SVG 优化", "status": "done", "message": ""},
                {"label": "导出品牌套件", "status": "done", "message": ""},
            ]}})
        if kit.get("status") == "failed":
            return jsonify({"code": 200, "data": {"status": "failed", "message": kit.get("error_message", "")}})
        return jsonify({"code": 200, "data": {"status": kit.get("status", "not_found")}})

    @app.route("/api/brand-kits/<int:kit_id>/download/<filename>", methods=["GET"])
    @jwt_required()
    def download_brand_kit_file_route(kit_id, filename):
        """Download an exported brand kit file."""
        try:
            kit = get_brand_kit(kit_id)
            if not kit or not kit.get("directory"):
                return jsonify({"code": 404, "message": "文件不存在"}), 404
            dir_path = os.path.join(os.path.dirname(__file__), kit["directory"])
            # Secure filename
            safe_name = os.path.basename(filename)
            file_path = os.path.join(dir_path, safe_name)
            if not os.path.isfile(file_path):
                return jsonify({"code": 404, "message": "文件不存在"}), 404
            return send_file(file_path, as_attachment=True)
        except Exception as e:
            logger.error(f"download_brand_kit_file: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- Proxy Pool (decodo) ----

    @app.route("/api/proxies", methods=["GET"])
    @jwt_required()
    def list_proxies_route():
        """List all proxies with occupancy info."""
        try:
            return jsonify({"code": 200, "data": list_proxies()})
        except Exception as e:
            logger.error(f"list_proxies: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/proxies/available", methods=["GET"])
    @jwt_required()
    def list_available_proxies_route():
        """List proxies not occupied by any brand kit."""
        try:
            return jsonify({"code": 200, "data": get_available_proxies()})
        except Exception as e:
            logger.error(f"available_proxies: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/proxies/deprecated", methods=["GET"])
    @jwt_required()
    def list_deprecated_proxies_route():
        """List deprecated proxies."""
        try:
            return jsonify({"code": 200, "data": list_deprecated_proxies()})
        except Exception as e:
            logger.error(f"deprecated_proxies: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/proxies/import", methods=["POST"])
    @jwt_required()
    def import_proxies_route():
        """Reseed proxies from current config (decodo or okkproxy)."""
        try:
            count = reseed_proxies()
            return jsonify({"code": 200, "message": f"已导入 {count} 条新代理", "data": {"imported": count}})
        except Exception as e:
            logger.error(f"import_proxies: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/proxies/import-text", methods=["POST"])
    @jwt_required()
    def import_proxies_text_route():
        """Import proxies from raw text (IP:PORT:USERNAME:PASSWORD format)."""
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"code": 400, "message": "代理文本不能为空"}), 400
        proxy_type = (data.get("proxy_type") or "http").strip()
        try:
            count = import_proxies_from_text(text, proxy_type)
            # Also save to global_config for persistence
            update_global_config("okkproxy_raw_list", text)
            return jsonify({"code": 200, "message": f"已导入 {count} 条代理", "data": {"imported": count}})
        except Exception as e:
            logger.error(f"import_proxies_text: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    # ---- 资源总览 (Admin: Google账户 + 指纹环境 分配管控) ----

    @app.route("/api/admin/resources", methods=["GET"])
    @jwt_required()
    def admin_resource_overview():
        """按运营角色分组，显示谷歌账户和指纹环境使用情况及成本。

        计费规则: 谷歌邮箱 1元/个, 指纹环境 2元/个
        """
        db = get_db()
        rows = db.execute("""
            SELECT bk.id AS kit_id, bk.name AS kit_name, bk.brand_name,
                bk.cloakbrowser_profile_name, bk.google_account_id, bk.proxy, bk.updated_at AS kit_updated_at,
                ga.email AS google_email, ga.country AS google_country, ga.updated_at AS google_updated_at,
                CASE WHEN ga.totp_secret IS NOT NULL AND ga.totp_secret != '' THEN 1 ELSE 0 END AS has_totp,
                u.id AS user_id, u.username AS operator_name,
                (SELECT COUNT(*) FROM sites WHERE brand_kit_id = bk.id) AS site_count
            FROM brand_kits bk
            LEFT JOIN google_accounts ga ON bk.google_account_id = ga.id
            LEFT JOIN users u ON bk.created_by = u.id
            ORDER BY u.username, bk.id
        """).fetchall()

        # Group by operator
        operators = {}
        for r in rows:
            uid = r["user_id"] or 0
            uname = r["operator_name"] or "未分配"
            if uid not in operators:
                operators[uid] = {
                    "user_id": uid, "operator_name": uname,
                    "kits": [], "google_count": 0, "profile_count": 0,
                    "google_cost": 0, "profile_cost": 0, "total_cost": 0,
                }
            op = operators[uid]
            op["kits"].append(dict(r))
            if r["google_email"]:
                op["google_count"] += 1
            if r["cloakbrowser_profile_name"]:
                op["profile_count"] += 1

        PRICE_GOOGLE = 1  # RMB per Google account
        PRICE_PROFILE = 2  # RMB per fingerprint profile

        for op in operators.values():
            op["google_cost"] = op["google_count"] * PRICE_GOOGLE
            op["profile_cost"] = op["profile_count"] * PRICE_PROFILE
            op["total_cost"] = op["google_cost"] + op["profile_cost"]

        free_ga = db.execute(
            "SELECT COUNT(*) FROM google_accounts WHERE occupied_kit_id IS NULL "
            "AND id NOT IN (SELECT google_account_id FROM brand_kits WHERE google_account_id IS NOT NULL)"
        ).fetchone()[0]

        return jsonify({"code": 200, "data": {
            "operators": list(operators.values()),
            "pricing": {"google": PRICE_GOOGLE, "profile": PRICE_PROFILE},
            "stats": {
                "total_google": sum(o["google_count"] for o in operators.values()),
                "total_profile": sum(o["profile_count"] for o in operators.values()),
                "total_cost": sum(o["total_cost"] for o in operators.values()),
                "free_google": free_ga,
            },
        }})

    # ---- Google Account Pool (GMC 2FA automation) ----

    @app.route("/api/google-accounts", methods=["GET"])
    @jwt_required()
    def list_google_accounts_route():
        """List all Google accounts with occupancy info (passwords masked)."""
        try:
            accounts = list_google_accounts()
            for a in accounts:
                pw = a.get("password", "")
                if pw:
                    a["password"] = pw[:3] + "***" if len(pw) > 3 else "***"
            return jsonify({"code": 200, "data": accounts})
        except Exception as e:
            logger.error(f"list_google_accounts: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/google-accounts/available", methods=["GET"])
    @jwt_required()
    def list_available_google_accounts_route():
        """List Google accounts not occupied by any brand kit."""
        try:
            return jsonify({"code": 200, "data": get_available_google_accounts()})
        except Exception as e:
            logger.error(f"available_google_accounts: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/google-accounts/import", methods=["POST"])
    @jwt_required()
    def import_google_accounts_route():
        """Import Google accounts from TXT text."""
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"code": 400, "message": "TXT 内容不能为空"}), 400
        try:
            count = import_google_accounts_from_txt(text)
            return jsonify({"code": 200, "message": f"已导入 {count} 个 Google 账户", "data": {"imported": count}})
        except Exception as e:
            logger.error(f"import_google_accounts: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/google-accounts/<int:account_id>", methods=["DELETE"])
    @jwt_required()
    def delete_google_account_route(account_id):
        """Delete a single Google account."""
        try:
            ok = delete_google_account(account_id)
            if not ok:
                return jsonify({"code": 404, "message": "账户不存在"}), 404
            return jsonify({"code": 200, "message": "账户已删除"})
        except Exception as e:
            logger.error(f"delete_google_account: {e}")
            return jsonify({"code": 500, "message": str(e)[:200]}), 500



    @app.route("/api/system/export", methods=["GET"])
    @jwt_required()
    def system_export():
        """Export all config and data as JSON."""
        try:
            db = get_db()
            data = {}
            def et(table, cols=None):
                c = "*" if cols is None else ", ".join(cols)
                return [dict(r) for r in db.execute(f"SELECT {c} FROM {table}").fetchall()]
            data["global_config"] = et("global_config", ["config_key", "config_value", "updated_at"])
            data["users"] = et("users", ["id", "username", "password", "role", "panel_environment_id", "created_at"])
            data["sites"] = et("sites")
            data["brand_kits"] = et("brand_kits")
            data["cloudflare_accounts"] = et("cloudflare_accounts")
            data["fingerprint_categories"] = et("fingerprint_categories")
            data["profile_category_mapping"] = et("profile_category_mapping")
            data["panel_environments"] = et("panel_environments")
            data["wordpress_settings"] = et("wordpress_settings")
            data["feed_products"] = et("feed_products")
            data["woocommerce_products"] = et("woocommerce_products")
            data["generated_feed"] = et("generated_feed")
            data["google_accounts"] = et("google_accounts")
            data["proxies"] = et("proxies")
            data["_meta"] = {"exported_at": datetime.utcnow().isoformat(), "version": "1.0"}
            return jsonify({"code": 200, "data": data})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500

    @app.route("/api/system/import", methods=["POST"])
    @jwt_required()
    def system_import():
        """Import config and data from JSON."""
        try:
            data = request.get_json(silent=True) or {}
            if not data or "_meta" not in data:
                return jsonify({"code": 400, "message": "invalid data"}), 400
            db = get_db()
            imported = []
            def cols(t):
                return {r["name"] for r in db.execute(f"PRAGMA table_info({t})").fetchall()}
            def up(table, rows, keys):
                if not rows: return 0
                valid = cols(table); cnt = 0
                for row in rows:
                    if not isinstance(row, dict): continue
                    f = {k: v for k, v in row.items() if k in valid}
                    if not f: continue
                    wp = [f"{k} = ?" for k in keys if k in f]
                    wv = [f[k] for k in keys if k in f]
                    if not wp: continue
                    w = " AND ".join(wp)
                    ex = db.execute(f"SELECT 1 FROM {table} WHERE {w}", wv).fetchone()
                    ck = list(f.keys()); cv = [f[k] for k in ck]
                    if ex:
                        sc = ", ".join(f"{k} = ?" for k in ck)
                        db.execute(f"UPDATE {table} SET {sc} WHERE {w}", cv + wv)
                    else:
                        ph = ", ".join("?" for _ in ck)
                        cn = ", ".join(ck)
                        db.execute(f"INSERT INTO {table} ({cn}) VALUES ({ph})", cv)
                    cnt += 1
                return cnt
            for t, k in [("global_config",["config_key"]),("users",["id"]),("panel_environments",["id"]),("cloudflare_accounts",["id"]),("fingerprint_categories",["id"]),("profile_category_mapping",["profile_name","category_id"]),("brand_kits",["id"]),("sites",["id"]),("wordpress_settings",["id"]),("feed_products",["id"]),("woocommerce_products",["id"]),("generated_feed",["id"]),("google_accounts",["id"]),("proxies",["id"])]:
                if t in data:
                    c = up(t, data[t], k)
                    imported.append(f"{t}({c})")
            db.commit()
            return jsonify({"code": 200, "message": "ok: " + ", ".join(imported)})
        except Exception as e:
            return jsonify({"code": 500, "message": str(e)[:200]}), 500









