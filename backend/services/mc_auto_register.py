"""
Google Merchant Center AI-driven registration + CloakBrowser profile management.

Uses DeepSeek AI to analyze each GMC page and decide the next action,
replacing the old hardcoded-selector approach.

Usage::

    from services.mc_auto_register import create_profile, register_gmc_ai

    cfg = create_profile("store-001", google_email="store@gmail.com",
                         proxy="socks5://user:pass@1.2.3.4:1080")

    result = asyncio.run(register_gmc_ai(
        profile_dir=cfg["dir"],
        site_url="example.com",
        google_email="store@gmail.com",
        google_password="xxx",
        google_totp_secret="JBSWY3DPEHPK3PXP",
        business_info={"company_name": "My Store", "city": "New York"},
        feed_url="https://example.com/feed.xml",
        log_callback=lambda level, msg, step: print(f"[{step}] {msg}"),
    ))
"""

import asyncio
import json
import logging
import os
import random
import re as _re
import string

import pyotp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 真实世界指纹池 — 下拉自流行硬件数据
# ---------------------------------------------------------------------------

_GPU_POOLS = {
    "win": {
        "vendors": ["Google Inc. (NVIDIA)", "Google Inc. (AMD)", "Google Inc. (Intel)"],
        "renderers": [
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (AMD, Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (Intel, UHD Graphics 730 Direct3D11 vs_5_0 ps_5_0)",
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)",
        ],
    },
    "mac": {
        "vendors": ["Apple Inc.", "Apple"],
        "renderers": [
            "Apple M1", "Apple M2", "Apple M3",
            "ANGLE (Apple, Apple M1 Pro)", "ANGLE (Apple, Apple M2 Max)",
        ],
    },
    "linux": {
        "vendors": ["NVIDIA Corporation", "AMD", "Intel"],
        "renderers": [
            "NVIDIA GeForce RTX 3060/PCIe/SSE2",
            "AMD Radeon RX 6700 XT (navi22, LLVM 15.0.7)",
            "Mesa Intel UHD Graphics 730 (ADL-S GT1)",
        ],
    },
}

_SCREEN_SIZES = [
    (1920, 1080),
    (2560, 1440),
    (1366, 768),
    (1536, 864),
    (1440, 900),
    (3840, 2160),
    (1680, 1050),
]

# 各地区的合理市区配置
_REGION_CONFIGS = {
    "US": {"timezones": ["America/New_York", "America/Chicago", "America/Los_Angeles", "America/Denver"], "locale": "en-US"},
    "GB": {"timezones": ["Europe/London"], "locale": "en-GB"},
    "DE": {"timezones": ["Europe/Berlin"], "locale": "de-DE"},
    "FR": {"timezones": ["Europe/Paris"], "locale": "fr-FR"},
    "JP": {"timezones": ["Asia/Tokyo"], "locale": "ja-JP"},
    "AU": {"timezones": ["Australia/Sydney"], "locale": "en-AU"},
    "CA": {"timezones": ["America/Toronto", "America/Vancouver"], "locale": "en-CA"},
}


def generate_fingerprint(platform: str | None = None, country: str = "US") -> dict:
    """生成一个随机但上下文一致的浏览器指纹配置。

    同一平台 + 国家的组合会从对应池子里随机抽取 GPU、屏幕等，
    确保指纹看起来像一台真实设备。

    Args:
        platform: "win" / "mac" / "linux"，None 则随机
        country: 国家代码，影响时区和语言

    Returns:
        dict 可直接序列化为 config.json
    """
    if platform is None:
        platform = random.choice(["win", "win", "win", "mac", "linux"])  # Windows 占多数

    region = _REGION_CONFIGS.get(country, _REGION_CONFIGS["US"])
    gpu_pool = _GPU_POOLS.get(platform, _GPU_POOLS["win"])

    vendor = random.choice(gpu_pool["vendors"])
    renderer = random.choice(gpu_pool["renderers"])
    width, height = random.choice(_SCREEN_SIZES)

    return {
        "platform": platform,
        "fingerprint_seed": "".join(random.choices(string.hexdigits.lower(), k=16)),
        "gpu_vendor": vendor,
        "gpu_renderer": renderer,
        "hardware_concurrency": random.choice([4, 8, 12, 16]),
        "device_memory": random.choice([4, 8, 16]),
        "screen_width": width,
        "screen_height": height,
        "timezone": random.choice(region["timezones"]),
        "locale": region["locale"],
        "browser_brand": random.choice(["Chrome", "Chrome", "Chrome", "Edge"]),
        "country": country,
        "webrtc_ip": "",
    }


# ---------------------------------------------------------------------------
# Profile 管理
# ---------------------------------------------------------------------------

def get_profiles_root() -> str:
    """返回 profiles 根目录的绝对路径。所有 profile 操作统一使用此函数。

    Docker 环境下 profiles 放在持久化数据卷 (WP_DATA_DIR) 下，避免重启丢失。
    """
    data_dir = os.environ.get("WP_DATA_DIR", "")
    if data_dir:
        return os.path.abspath(os.path.join(data_dir, "profiles"))
    return os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles"))


def resolve_profile_path(name: str) -> str:
    """解析 profile 名称到完整路径。

    - 如果 name 已是绝对路径且目录存在，直接返回
    - 否则相对于 get_profiles_root() 拼接并检查
    - 目录不存在时抛出 FileNotFoundError
    """
    if os.path.isabs(name) and os.path.isdir(name):
        return os.path.abspath(name)
    full = os.path.abspath(os.path.join(get_profiles_root(), name))
    if not os.path.isdir(full):
        raise FileNotFoundError(f"Profile 目录不存在: {full}")
    return full


def load_profile_config(profile_dir: str) -> dict | None:
    """读取 profile 目录的 config.json。"""
    cfg_path = os.path.join(profile_dir, "config.json")
    if os.path.isfile(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_profile_config(profile_dir: str, config: dict) -> None:
    """写入 config.json 到 profile 目录。"""
    os.makedirs(profile_dir, exist_ok=True)
    cfg_path = os.path.join(profile_dir, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def create_profile(
    name: str,
    google_email: str = "",
    proxy: str = "",
    country: str = "US",
    platform: str | None = None,
) -> dict:
    """创建一个新的 CloakBrowser profile 目录 + config.json。

    Args:
        name: 目录名（如 "store-001"）
        google_email: 绑定的 Google 账号（用于记录）
        proxy: 代理 URL（socks5://user:pass@host:port）
        country: 国家代码
        platform: "win"/"mac"/"linux"，None=随机

    Returns:
        {"dir": "...", "config": {...}}
    """
    root = get_profiles_root()
    profile_dir = os.path.join(root, name)

    if os.path.isdir(profile_dir) and os.path.isfile(os.path.join(profile_dir, "config.json")):
        raise FileExistsError(f"Profile '{name}' 已存在")

    config = generate_fingerprint(platform, country)
    config["google_email"] = google_email
    config["proxy"] = proxy if proxy else ""
    config["note"] = ""

    save_profile_config(profile_dir, config)
    logger.info("Profile created: %s (platform=%s, country=%s)", name, config["platform"], country)

    return {"dir": profile_dir, "config": config}


def update_profile(name: str, **kwargs) -> dict:
    """更新已有 profile 的配置字段。

    可更新的字段：google_email, proxy, note, fingerprint_seed,
    platform, gpu_vendor, gpu_renderer, hardware_concurrency,
    device_memory, screen_width, screen_height, timezone, locale,
    browser_brand, country, webrtc_ip
    """
    root = get_profiles_root()
    profile_dir = os.path.join(root, name)

    config = load_profile_config(profile_dir)
    if config is None:
        raise FileNotFoundError(f"Profile '{name}' 不存在或缺少 config.json")

    allowed = {
        "google_email", "proxy", "note", "fingerprint_seed",
        "platform", "gpu_vendor", "gpu_renderer", "hardware_concurrency",
        "device_memory", "screen_width", "screen_height", "timezone",
        "locale", "browser_brand", "country", "webrtc_ip",
    }
    for k, v in kwargs.items():
        if k in allowed and v is not None:
            config[k] = v

    save_profile_config(profile_dir, config)
    logger.info("Profile updated: %s", name)
    return {"dir": profile_dir, "config": config}


def delete_profile(name: str) -> bool:
    """删除整个 profile 目录（含 cookies 和 config）。"""
    import shutil
    root = get_profiles_root()
    profile_dir = os.path.join(root, name)
    if os.path.isdir(profile_dir):
        shutil.rmtree(profile_dir)
        logger.info("Profile deleted: %s", name)
        return True
    return False


def list_profiles() -> list[dict]:
    """列出所有 profile，返回完整配置信息。"""
    root = get_profiles_root()
    profiles = []
    if not os.path.isdir(root):
        return profiles
    for name in sorted(os.listdir(root)):
        pdir = os.path.join(root, name)
        if not os.path.isdir(pdir):
            continue
        config = load_profile_config(pdir) or {}
        has_cookies = os.path.isdir(os.path.join(pdir, "Default"))
        profiles.append({
            "id": pdir,
            "name": name,
            "dir": pdir,
            "config": config,
            "google_email": config.get("google_email", ""),
            "proxy": config.get("proxy", ""),
            "platform": config.get("platform", "unknown"),
            "country": config.get("country", ""),
            "gpu": config.get("gpu_renderer", ""),
            "screen": f"{config.get('screen_width', '?')}x{config.get('screen_height', '?')}",
            "fingerprint_seed": config.get("fingerprint_seed", ""),
            "timezone": config.get("timezone", ""),
            "locale": config.get("locale", ""),
            "note": config.get("note", ""),
            "has_cookies": has_cookies,
        })
    return profiles


# ---------------------------------------------------------------------------
# CloakBrowser 启动参数构建
# ---------------------------------------------------------------------------

def _build_launch_args(config: dict) -> list[str]:
    """根据 config.json 构建 CloakBrowser 指纹启动参数列表。"""
    args = ["--no-sandbox"]
    platform = config.get("platform", "")

    if config.get("fingerprint_seed"):
        args.append(f"--fingerprint={config['fingerprint_seed']}")

    # CloakBrowser v14+ auto-generates GPU/screen/hardware from seed.
    # Only --fingerprint, --fingerprint-platform, --fingerprint-webrtc-ip are supported.
    if platform == "mac":
        args.append("--fingerprint-platform=macos")
    else:
        args.append("--fingerprint-platform=windows")

    if config.get("webrtc_ip"):
        args.append(f"--fingerprint-webrtc-ip={config['webrtc_ip']}")

    return args


def _unlock_profile(profile_dir: str):
    """Remove Chromium SingletonLock files left by a crashed process."""
    for fname in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = os.path.join(profile_dir, fname)
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _normalize_proxy_for_launch(proxy: str, bypass_inline_auth: bool = False):
    """Convert an HTTP proxy URL string to a dict for CloakBrowser.

    Passing HTTP proxies as {server, username, password} avoids URL-parsing
    edge cases in CloakBrowser's _resolve_proxy_config and ensures credentials
    are properly percent-encoded by _reconstruct_http_url.

    When bypass_inline_auth=True, credentials are embedded in the server URL
    (no separate username/password keys), so CloakBrowser's _has_credentials
    returns False → Playwright's CDP auth handles the proxy instead of
    Chromium's preemptive auth. This avoids misleading ERR_INVALID_AUTH_CREDENTIALS
    when the proxy returns non-auth errors like 403 geo-block.

    SOCKS5 and proxy-without-credentials are returned as-is (string).
    """
    if not proxy:
        return None
    p = proxy.replace("socks5h://", "socks5://")
    if p.startswith("http://") and "@" in p:
        try:
            from urllib.parse import urlparse, unquote, quote
            parsed = urlparse(p)
            if parsed.username:
                if bypass_inline_auth:
                    # Embed credentials in server URL → bypasses inline auth
                    enc_user = quote(unquote(parsed.username), safe="")
                    enc_pass = quote(unquote(parsed.password or ""), safe="")
                    server = f"{parsed.scheme}://{enc_user}:{enc_pass}@{parsed.hostname}"
                    if parsed.port:
                        server += f":{parsed.port}"
                    logger.info("HTTP proxy dict (inline auth bypassed): server=%s", server[:50] + "...")
                    return {"server": server}
                else:
                    server = f"{parsed.scheme}://{parsed.hostname}"
                    if parsed.port:
                        server += f":{parsed.port}"
                    result = {
                        "server": server,
                        "username": unquote(parsed.username),
                        "password": unquote(parsed.password) if parsed.password else "",
                    }
                    logger.info("Normalized HTTP proxy to dict: server=%s username=%s", server, result["username"])
                    return result
        except Exception as e:
            logger.warning("Failed to parse HTTP proxy URL, passing through: %s", e)
    return p


# ---------------------------------------------------------------------------
# MC 注册自动化 — GMC Next 辅助函数
# ---------------------------------------------------------------------------

from datetime import datetime as _dt

async def _detect_gmc_page(page) -> dict:
    """Detect current GMC page state. Returns dict with phase and step info.

    Phases:
      - "login": Google login page (redirected)
      - "wizard": GMC registration wizard (12 steps)
      - "dashboard": GMC dashboard (already registered)
      - "setup": GMC setup/products flow
      - "unknown": cannot determine
    """
    url = page.url or ""
    content = (await page.content())[:8000] if page.url else ""

    state = {"phase": "unknown", "wizard_step": 0, "setup_step": 0, "url": url[:120]}

    # 1. Google login detection
    if "accounts.google.com" in url:
        state["phase"] = "login"
        return state

    # 2. GMC setup flow
    if "/mc/setup/" in url or "flow=onlineOnboarding" in url:
        state["phase"] = "setup"
        # Try to detect step from URL or page indicators
        m = __import__('re').search(r'/mc/setup/(\w+)', url)
        if m:
            state["setup_subpage"] = m.group(1)
        return state

    # 3. GMC dashboard (already registered)
    dashboard_indicators = [
        "Performance", "Dashboard", "Products", "All products",
        "diagnostics", "Growth", "Business information",
    ]
    dashboard_count = sum(1 for kw in dashboard_indicators if kw.lower() in content.lower())
    if dashboard_count >= 3:
        state["phase"] = "dashboard"
        return state

    # Also check for GMC home/dashboard URL patterns
    if "/mc/" not in url and "merchants.google.com" in url:
        # May be on the home/dashboard page
        if "overview" in content.lower() or "get started" not in content.lower():
            state["phase"] = "dashboard"
            return state

    # 4. Registration wizard detection
    wizard_indicators = [
        "Do you sell products online",
        "sell products online",
        "online store",
        "store URL",
        "website URL",
        "merchant display name",
        "business display name",
        "company name",
        "physical store",
        "HTML tag",
        "verify your website",
        "claim your website",
        "target country",
        "feed URL",
        "data source",
        "shipping and delivery",
        "return policy",
        "return window",
        "restocking fee",
        "claim your URL",
        "Google Merchant Center",
    ]
    wizard_count = sum(1 for kw in wizard_indicators if kw.lower() in content.lower())

    # Detect specific wizard step
    if "sell products online" in content.lower():
        state["wizard_step"] = 1
    elif "store URL" in content.lower() or "website URL" in content.lower():
        state["wizard_step"] = 2
    elif "physical store" in content.lower():
        state["wizard_step"] = 3
    elif "company name" in content.lower() or "business display name" in content.lower() or "merchant display name" in content.lower():
        state["wizard_step"] = 4
    elif "business address" in content.lower() or "street address" in content.lower():
        state["wizard_step"] = 5
    elif "HTML tag" in content.lower() or "verify your website" in content.lower():
        state["wizard_step"] = 6
    elif "target country" in content.lower() or "sales country" in content.lower():
        state["wizard_step"] = 7
    elif "feed" in content.lower() or "data source" in content.lower():
        state["wizard_step"] = 8
    elif "shipping" in content.lower():
        state["wizard_step"] = 9
    elif "return policy" in content.lower() or "return window" in content.lower():
        state["wizard_step"] = 10
    elif "claim" in content.lower() or "complete" in content.lower():
        state["wizard_step"] = 11

    if wizard_count >= 2 or state["wizard_step"] > 0:
        state["phase"] = "wizard"
        return state

    # 5. Fallback: try detecting based on buttons/inputs
    try:
        has_continue = await page.locator("button:has-text('Continue'), button:has-text('Next')").count() > 0
        has_input = await page.locator("input:visible, mwc-textfield:visible, md-outlined-text-field:visible").count() > 0
        if has_continue or has_input:
            state["phase"] = "wizard"  # probably on some form page
    except Exception:
        pass

    return state


async def _dismiss_overlays(page):
    """Dismiss cookie consent, promotions, and other blocking overlays."""
    for sel in [
        "button:has-text('Accept all')", "button:has-text('I agree')",
        "button:has-text('OK')", "button:has-text('Got it')",
        "button[aria-label='Close']", "button[aria-label='Dismiss']",
        "[aria-label='Close dialog']", "button[aria-label='No thanks']",
        "a:has-text('Skip')", "a:has-text('Not now')",
    ]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click(timeout=3000)
                await asyncio.sleep(0.5)
        except Exception:
            pass


# DOM reconnaissance JS — shared between recon mode and register flow
_DUMP_DOM_JS = r"""() => {
    const R = {
        url: location.href, title: document.title, stepHint: '',
        inputs: [], selects: [], textareas: [], buttons: [], radios: [],
        checkboxes: [], mwcEls: [], headings: [], labels: [], helpTexts: [],
    };
    // Step indicator
    document.querySelectorAll('[data-step], [aria-current="step"], .step-indicator, ' +
        'mat-step-header, [role="tablist"] [aria-selected="true"], mwc-tab[active], ' +
        '.progress-indicator span, .stepper .active, nav[aria-label*="step"] span').forEach(el => {
        const t = el.textContent?.trim()?.substring(0, 200);
        if (t) R.stepHint += t + ' | ';
    });
    // Visible inputs
    document.querySelectorAll('input:not([type="hidden"])').forEach(el => {
        if (!el.offsetParent) return;
        const label = el.closest('label')?.textContent?.trim()?.substring(0, 80) || '';
        const parent = el.parentElement;
        const wrapper = el.closest('[class*="field"], [class*="input"], [class*="form"]');
        R.inputs.push({
            type: el.type, name: el.name, id: el.id,
            placeholder: el.placeholder, value: el.value?.substring(0, 50),
            ariaLabel: el.getAttribute('aria-label'), required: el.required,
            label: label,
            parentText: parent?.textContent?.trim()?.substring(0, 100) || '',
            wrapperText: wrapper?.textContent?.trim()?.replace(/\s+/g,' ').substring(0, 150) || '',
        });
    });
    // Visible selects
    document.querySelectorAll('select').forEach(el => {
        if (!el.offsetParent) return;
        R.selects.push({
            name: el.name, id: el.id, ariaLabel: el.getAttribute('aria-label'),
            options: Array.from(el.options).slice(0, 20).map(o => o.text?.trim()),
        });
    });
    // Textareas
    document.querySelectorAll('textarea').forEach(el => {
        if (!el.offsetParent) return;
        R.textareas.push({
            name: el.name, id: el.id, placeholder: el.placeholder,
            ariaLabel: el.getAttribute('aria-label'),
        });
    });
    // Buttons
    document.querySelectorAll('button, [role="button"], a[role="button"]').forEach(el => {
        if (!el.offsetParent) return;
        R.buttons.push({
            text: el.textContent?.trim()?.substring(0, 100),
            type: el.type, name: el.name, id: el.id,
            ariaLabel: el.getAttribute('aria-label'), disabled: el.disabled,
        });
    });
    // Radios (input + role-based)
    document.querySelectorAll('input[type="radio"]').forEach(el => {
        const label = el.closest('label')?.textContent?.trim()?.substring(0, 80);
        const pText = el.parentElement?.textContent?.trim()?.substring(0, 80);
        R.radios.push({
            name: el.name, value: el.value, checked: el.checked,
            label: label || pText, visible: el.offsetParent !== null,
        });
    });
    document.querySelectorAll('[role="radio"]').forEach(el => {
        if (!el.offsetParent) return;
        R.radios.push({
            role: 'radio', text: el.textContent?.trim()?.substring(0, 100),
            checked: el.getAttribute('aria-checked'),
        });
    });
    // Checkboxes
    document.querySelectorAll('input[type="checkbox"]').forEach(el => {
        const label = el.closest('label')?.textContent?.trim()?.substring(0, 80);
        R.checkboxes.push({
            name: el.name, value: el.value, checked: el.checked, label,
        });
    });
    // MWC elements
    document.querySelectorAll('mwc-textfield, mwc-select, mwc-button, ' +
        'mwc-radio, mwc-checkbox, mwc-textarea, mwc-outlined-text-field, ' +
        'md-outlined-text-field, md-filled-text-field, md-select, md-radio, ' +
        'md-checkbox, md-filled-button, md-outlined-button').forEach(el => {
        const attrs = {};
        for (const a of el.attributes) attrs[a.name] = a.value?.substring(0, 100);
        R.mwcEls.push({
            tag: el.tagName.toLowerCase(),
            label: el.getAttribute('label') || el.getAttribute('aria-label'),
            value: el.getAttribute('value'), disabled: el.hasAttribute('disabled'),
            attrs: attrs,
        });
    });
    // Headings
    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
        if (!el.offsetParent) return;
        R.headings.push({ tag: el.tagName, text: el.textContent?.trim()?.substring(0, 200) });
    });
    // Help text
    document.querySelectorAll('[class*="help"], [class*="hint"], [class*="helper"], ' +
        '[class*="description"], [class*="supporting"], [class*="secondary"], ' +
        '[class*="subtitle"], [class*="assistant"]').forEach(el => {
        if (!el.offsetParent) return;
        const t = el.textContent?.trim();
        if (t && t.length > 3 && t.length < 500) R.helpTexts.push(t);
    });
    // All labels
    document.querySelectorAll('label').forEach(el => {
        if (!el.offsetParent) return;
        R.labels.push(el.textContent?.trim()?.substring(0, 200));
    });
    return JSON.parse(JSON.stringify(R));
}"""


async def _get_logged_in_email(page) -> str:
    """Return the email of the currently logged-in Google account, or '' if undetectable."""
    try:
        # Navigate to Google account page to see logged-in email
        await page.goto("https://myaccount.google.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        for sel in [
            "[aria-label*='Google Account']", "img[src*='googleaccount']",
            "div[data-email]", "a[aria-label*='@']",
        ]:
            el = page.locator(sel).first
            try:
                text = await el.get_attribute("aria-label") or await el.text_content()
                if text and "@" in text:
                    return text.strip()
            except Exception:
                pass
        # Fallback: check page text for email pattern
        body = await page.inner_text("body")
        import re
        m = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', body)
        if m:
            return m.group(0)
    except Exception:
        pass
    return ""


async def _google_login(
    page,
    email: str,
    password: str,
    totp_secret: str = "",
    log_callback=None,
    timeout_ms: int = 180000,
    recon_dir: str = "",
) -> bool:
    """Perform Google login on the given Playwright page. Returns True on success.

    If recon_dir is set, dumps screenshot+DOM at each login phase.
    """

    def _emit(level: str, msg: str, step: str = ""):
        log_func = {"info": logger.info, "warning": logger.warning, "error": logger.error}.get(level, logger.info)
        log_func("%s", msg)
        if log_callback:
            try:
                log_callback(level, msg, step)
            except Exception:
                pass

    _emit("info", f"Google 登录: {email}", "google_login")

    # --- Phase 1: Email page ---
    if recon_dir:
        await _dump_step_dom(page, recon_dir, "google_login_01_email")
    _emit("info", "填写邮箱...", "google_login")
    for sel in [
        "input[type='email']", "input[name='identifier']",
        "input[aria-label*='Email']", "input[aria-label*='email']",
    ]:
        inp = page.locator(sel)
        if await inp.count() > 0:
            await inp.first.fill(email)
            await asyncio.sleep(1)
            _emit("info", f"已填写邮箱: {email}", "google_login")
            break

    for btn_text in ["Next", "Continue"]:
        btn = page.locator(f"button:has-text('{btn_text}')")
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)
            _emit("info", f"点击了 '{btn_text}'", "google_login")
            break

    # --- Phase 2: Password page ---
    if recon_dir:
        await _dump_step_dom(page, recon_dir, "google_login_02_password")
    _emit("info", "填写密码...", "google_login")
    for sel in [
        "input[type='password']", "input[name='password']", "input[name='Passwd']",
        "input[aria-label*='password']", "input[aria-label*='Password']",
    ]:
        inp = page.locator(sel)
        if await inp.count() > 0:
            await inp.first.fill(password)
            await asyncio.sleep(1)
            _emit("info", "已填写密码", "google_login")
            break

    for btn_text in ["Next", "Sign in"]:
        btn = page.locator(f"button:has-text('{btn_text}')")
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)
            _emit("info", f"点击了 '{btn_text}'", "google_login")
            break

    # --- Phase 3: 2FA prompt ---
    await asyncio.sleep(2)
    content = await page.content()
    if totp_secret and ("2-Step Verification" in content or "authenticator" in content.lower() or "verify" in page.url.lower()):
        if recon_dir:
            await _dump_step_dom(page, recon_dir, "google_login_03_2fa")
        _emit("info", "检测到 2FA 验证，正在生成 TOTP 验证码...", "google_login")
        try:
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            _emit("info", f"已生成 TOTP 验证码", "google_login")
        except Exception as e:
            _emit("error", f"TOTP 生成失败: {e}", "google_login")
            return False

        for sel in [
            "input[type='tel']", "input[aria-label*='code']",
            "input[aria-label*='verification']", "input[name='totpPin']",
            "input[aria-label*='G-']",
        ]:
            inp = page.locator(sel)
            if await inp.count() > 0:
                await inp.first.fill(code)
                await asyncio.sleep(1)
                _emit("info", "已填写 TOTP 验证码", "google_login")
                break

        for btn_text in ["Next", "Verify", "Continue"]:
            btn = page.locator(f"button:has-text('{btn_text}')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(4)
                _emit("info", "已提交 2FA 验证码", "google_login")
                break

    # --- Phase 4: Extra verification prompts ---
    await asyncio.sleep(2)
    content = await page.content()
    if "recovery email" in content.lower():
        if recon_dir:
            await _dump_step_dom(page, recon_dir, "google_login_04_recovery")
        _emit("warning", "检测到恢复邮箱确认提示，尝试跳过...", "google_login")
        for btn_text in ["Confirm", "Skip", "Not now", "Later"]:
            btn = page.locator(f"button:has-text('{btn_text}'), a:has-text('{btn_text}')")
            if await btn.count() > 0:
                await btn.first.click()
                await asyncio.sleep(2)
                break

    if "verify it's you" in content.lower():
        if recon_dir:
            await _dump_step_dom(page, recon_dir, "google_login_05_verify_you")
        _emit("warning", "检测到身份验证提示，尝试关闭...", "google_login")
        for btn_text in ["Skip", "Not now", "Later", "Cancel"]:
            btn = page.locator(f"button:has-text('{btn_text}'), a:has-text('{btn_text}')")
            if await btn.count() > 0:
                await btn.first.click()
                await asyncio.sleep(2)
                break

    # --- Wait for successful redirect ---
    _emit("info", "等待登录完成...", "google_login")
    for i in range(10):
        await asyncio.sleep(2)
        if "accounts.google.com" not in page.url and "google.com" in page.url:
            if recon_dir:
                await _dump_step_dom(page, recon_dir, "google_login_06_done")
            _emit("info", f"登录成功! URL: {page.url[:80]}", "google_login")
            return True
        if "Wrong password" in await page.content():
            _emit("error", "密码错误", "google_login")
            return False

    # Check if login succeeded: we're on a Google service page (GMC, business, etc)
    current_url = page.url
    success = (
        "accounts.google.com" not in current_url
        or "/signin/" not in current_url  # Past signin, maybe on consent/check page
    )
    # Additional check: if we see merchant or business Google URLs
    if not success:
        if any(d in current_url for d in ["merchants.google.com", "business.google.com", "myaccount.google.com"]):
            success = True
    if success:
        _emit("info", "Google 登录完成", "google_login")
    else:
        _emit("warning", f"可能仍在登录页面: {current_url[:80]}", "google_login")
    return success


# ---------------------------------------------------------------------------
# AI-driven GMC registration
# ---------------------------------------------------------------------------

async def _dump_dom_json(page, log_callback=None):
    """Extract structured page DOM as JSON text for AI analysis."""
    try:
        dom_raw = await page.evaluate(_DUMP_DOM_JS)
        dom = json.loads(dom_raw) if isinstance(dom_raw, str) else dom_raw

        parts = []
        parts.append(f"URL: {dom.get('url', page.url)}")
        parts.append(f"Title: {dom.get('title', '')}")
        parts.append(f"Heading: {dom.get('heading', '')}")

        inputs = dom.get('inputs', [])
        if inputs:
            parts.append(f"\nInputs ({len(inputs)}):")
            for inp in inputs[:20]:
                parts.append(f"  [{inp.get('type','text')}] label={inp.get('label','?')} placeholder={inp.get('placeholder','')} id={inp.get('id','')}")

        buttons = dom.get('buttons', [])
        if buttons:
            parts.append(f"\nButtons ({len(buttons)}):")
            for btn in buttons[:20]:
                parts.append(f"  text={btn.get('text','')} selector={btn.get('selector','')}")

        selects = dom.get('selects', [])
        if selects:
            parts.append(f"\nSelects ({len(selects)}):")
            for sel in selects[:10]:
                parts.append(f"  label={sel.get('label','')} options={sel.get('options','')[:5]}")

        links = dom.get('links', [])
        if links:
            parts.append(f"\nLinks ({len(links)}):")
            for link in links[:15]:
                parts.append(f"  text={link.get('text','')} href={link.get('href','')}")

        errors = dom.get('errors', [])
        if errors:
            parts.append(f"\nErrors: {'; '.join(errors[:5])}")

        return '\n'.join(parts)
    except Exception as e:
        logger.warning(f"_dump_dom_json failed: {e}")
        return f"URL: {page.url}\nTitle: {await page.title()}\n(dom extraction failed: {e})"


def _build_ai_prompt(dom_text, site_url, business_info, feed_url, completed_steps, page_title):
    """Build DeepSeek prompt for GMC automation decision."""
    biz_str = json.dumps(business_info or {}, ensure_ascii=False)
    steps_str = ', '.join(completed_steps) if completed_steps else '(none)'

    return f"""You are a Google Merchant Center automation assistant. Help register a new merchant account.

TASK: Register GMC for website: {site_url}
Business info: {biz_str}
Feed URL: {feed_url}
Steps completed: {steps_str}

CURRENT PAGE:
{dom_text}

Return ONLY JSON (no markdown, no explanation outside JSON):
{{
  "action": "click" | "fill" | "select" | "navigate" | "wait" | "done" | "fail",
  "selector": "CSS selector or visible button/link text",
  "value": "value to fill (only for fill/select/navigate actions)",
  "reasoning": "brief explanation"
}}

RULES:
- If this is a form asking for business info, fill it using the provided business_info JSON
- Country should be United States unless business_info says otherwise
- Store website URL is: {site_url}
- Feed/product URL is: {feed_url}
- Shipping and returns policy pages: just click continue/next/skip
- If you see a captcha, verification challenge, or unexpected error: return action=fail
- If you see GMC dashboard, MC account ID (numeric), or success message: return action=done
- Use visible button/link TEXT as selector when possible (e.g. "Next", "Continue", "Save")"""


async def _call_deepseek_for_action(prompt, log_callback=None):
    """Call DeepSeek API to get the next action. Returns parsed action dict."""
    from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
    import requests as http_requests

    keys = get_deepseek_keys()
    if not keys:
        raise RuntimeError("No DeepSeek API keys configured")

    def _call(key):
        resp = http_requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "You are a browser automation AI. You MUST return ONLY valid JSON. No markdown, no explanation outside the JSON object."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            body = resp.json()
            return body["choices"][0]["message"]["content"].strip()
        resp.raise_for_status()

    raw = rotate_deepseek(_call, keys)

    # Extract JSON from response (may have markdown backticks)
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[^{}]*"action"[^{}]*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"DeepSeek returned non-JSON: {raw[:300]}")


async def _execute_action(page, action, log_callback=None):
    """Execute an AI-suggested action on the page."""
    act = action["action"]
    selector = action.get("selector", "")
    value = action.get("value", "")
    reasoning = action.get("reasoning", "")

    if log_callback and reasoning:
        log_callback("info", f"AI: {reasoning}", "ai_reason")

    if act == "click" and selector:
        # Multiple strategies to find and click the element
        strategies = [
            ("css", lambda: page.locator(selector).first),
            ("text", lambda: page.get_by_text(selector, exact=False).first),
            ("role_button", lambda: page.get_by_role("button", name=selector).first),
            ("role_link", lambda: page.get_by_role("link", name=selector).first),
        ]
        clicked = False
        for strat_name, strat_fn in strategies:
            try:
                el = strat_fn()
                if await el.count() > 0 and await el.is_visible():
                    await el.click(timeout=5000)
                    if log_callback: log_callback("info", f"Click({strat_name}): {selector}", "click")
                    await asyncio.sleep(2)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            # Last resort: force click via JavaScript by text content
            try:
                await page.evaluate(f"""
                    const els = document.querySelectorAll('a, button, [role=\"button\"], [role=\"link\"]');
                    for (const el of els) {{
                        if (el.textContent.includes('{selector}')) {{ el.click(); break; }}
                    }}
                """)
                if log_callback: log_callback("info", f"Click(js): {selector}", "click")
                await asyncio.sleep(2)
                clicked = True
            except Exception:
                pass
        if not clicked:
            raise RuntimeError(f"Cannot click: {selector}")

    elif act == "fill" and selector and value:
        try:
            # Try by label/placeholder/name
            el = page.locator(f"input[aria-label*='{selector}'], input[placeholder*='{selector}'], input[name*='{selector}']").first
            if await el.count() == 0:
                el = page.locator(selector).first
            if await el.count() > 0:
                await el.fill(value, timeout=5000)
                if log_callback: log_callback("info", f"Fill: {selector} = {value[:50]}", "fill")
                await asyncio.sleep(1)
                return
        except Exception:
            pass
        raise RuntimeError(f"Cannot fill: {selector}")

    elif act == "select" and selector and value:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                await el.select_option(value, timeout=5000)
                if log_callback: log_callback("info", f"Select: {selector} = {value}", "select")
                await asyncio.sleep(1)
                return
        except Exception:
            pass
        raise RuntimeError(f"Cannot select: {selector}")

    elif act == "navigate" and value:
        if log_callback: log_callback("info", f"Navigate: {value}", "navigate")
        await page.goto(value, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

    elif act == "wait":
        secs = min(int(value) if value else 3, 10)
        if log_callback: log_callback("info", f"Wait {secs}s", "wait")
        await asyncio.sleep(secs)

    elif act in ("done", "fail"):
        pass

    else:
        raise RuntimeError(f"Unknown action: {act}")


async def _extract_mc_id(page):
    """Try to extract GMC account ID from page."""
    try:
        body = await page.inner_text("body")
        import re
        matches = re.findall(r'(?:MC|account|merchant).*?(\d{7,12})', body, re.IGNORECASE)
        if matches:
            return matches[0]
        if "merchant_id=" in page.url:
            return page.url.split("merchant_id=")[1].split("&")[0]
    except Exception:
        pass
    return ""


async def register_gmc_ai(
    profile_dir: str,
    site_url: str,
    google_email: str = "",
    google_password: str = "",
    google_totp_secret: str = "",
    business_info: dict = None,
    feed_url: str = "",
    headless: bool = True,
    timeout_ms: int = 180000,
    log_callback = None,
) -> dict:
    """AI-driven GMC (Google Merchant Center) registration.

    Launches CloakBrowser, handles Google login, then uses DeepSeek AI
    to analyze each page and decide the next action. Loops until registration
    is complete or fails.

    Returns: {"success": bool, "mc_account_id": str, "message": str, "steps": int}
    """
    _emit = log_callback or (lambda level, msg, step=None: logger.info(f"[{step or 'gmc'}] {msg}"))

    # Step 1: Load config and launch browser
    _emit("info", "Loading profile config...", "config")
    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")

    tz = config.get("timezone", "America/Chicago")
    locale_str = config.get("locale", "en-US")
    fingerprint_args = _build_launch_args(config)

    proxy_display = proxy[:40] + "..." if len(proxy) > 40 else (proxy or "(none)")
    _emit("info", f"Profile: {os.path.basename(profile_dir)} | proxy={proxy_display}", "config")

    from cloakbrowser import launch_persistent_context_async

    launch_kwargs = {
        "headless": headless,
        "user_data_dir": profile_dir,
        "timeout": timeout_ms,
        "args": fingerprint_args,
    }
    if proxy:
        launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)

    _emit("info", "Launching CloakBrowser...", "launch")
    _unlock_profile(profile_dir)  # Remove stale lock files from previous crashes
    context = page = None
    try:
        context = await launch_persistent_context_async(**launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        _emit("info", "Browser launched successfully", "launch")
    except Exception as e:
        _emit("error", f"Browser launch failed: {e}", "launch")
        return {"success": False, "message": f"Browser launch failed: {e}", "steps": 0}

    try:
        # Step 2: Navigate to GMC (force English locale)
        _emit("info", "Navigating to Google Merchant Center...", "navigate")
        page.set_default_navigation_timeout(90000)
        await page.goto("https://merchants.google.com/mc/setup", wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(3)
        await _dismiss_overlays(page)

        # Step 3: Check login status
        state = await _detect_gmc_page(page)
        _emit("info", f"Page: phase={state['phase']} url={str(state.get('url',''))[:80]}", "detect")

        if state["phase"] == "login" or "accounts.google.com" in page.url:
            if google_email and google_password:
                _emit("info", f"Logging in as {google_email}...", "login")
                logged_in = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=120000
                )
                email_on_page = await _get_logged_in_email(page)
                if email_on_page:
                    _emit("info", f"Logged in as: {email_on_page}", "login")
                elif not logged_in:
                    _emit("error", "Google login failed", "login")
                    return {"success": False, "message": "Google login failed", "steps": 1}
                await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                await _dismiss_overlays(page)
            else:
                _emit("error", "No Google credentials provided", "login")
                return {"success": False, "message": "No Google credentials", "steps": 1}

        # Step 4: AI-driven loop
        MAX_STEPS = 50
        completed_steps = []
        mc_account_id = ""
        step_num = 0
        last_url = ""
        same_action_count = 0
        last_action = ""

        for step_num in range(1, MAX_STEPS + 1):
            _emit("info", f"--- AI Step {step_num}/{MAX_STEPS} ---", "ai_loop")

            # Stuck detection: same action 3+ times without URL change
            current_url = page.url
            if current_url == last_url and last_action:
                same_action_count += 1
            else:
                same_action_count = 0
                last_url = current_url
            if same_action_count >= 3:
                _emit("warning", f"Stuck detected (same page x{same_action_count}), checking for popup/login...", "stuck")
                # Check if a popup window was opened by the sign-in click
                if len(context.pages) > 1:
                    popup = context.pages[-1]
                    if "accounts.google.com" in popup.url:
                        _emit("info", f"Found Google login popup: {popup.url[:80]}", "popup")
                        logged_in = await _google_login(
                            popup, google_email, google_password, google_totp_secret,
                            log_callback=log_callback, timeout_ms=120000
                        )
                        if logged_in:
                            _emit("info", "Popup login successful", "popup")
                            await asyncio.sleep(2)
                            # Return to main page
                            page.bring_to_front()
                            await page.reload(wait_until="domcontentloaded", timeout=30000)
                    elif "google.com" in popup.url:
                        _emit("info", f"Popup on Google domain, switching to it...", "popup")
                        popup.bring_to_front()
                        try:
                            await popup.close()
                        except Exception:
                            pass
                else:
                    # No popup, try direct Google login
                    if google_email and google_password:
                        _emit("info", "No popup found, trying direct Google login...", "stuck")
                        await page.goto("https://accounts.google.com/signin/v2/identifier?service=merchantcenter&continue=https://merchants.google.com/", wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(3)
                same_action_count = 0

            # Check if we landed on Google login after previous action
            if "accounts.google.com" in page.url and google_email and google_password:
                _emit("info", "Detected Google login page, logging in...", "login_detect")
                logged_in = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=120000
                )
                if logged_in:
                    _emit("info", "Google login successful, resuming...", "login_detect")
                    # Wait for Google redirect to GMC (can be slow via proxy)
                    for retry in range(20):
                        await asyncio.sleep(2)
                        if "merchants.google.com" in page.url or "business.google.com" in page.url:
                            _emit("info", f"Redirected to: {page.url[:80]}", "redirect")
                            break
                    if "accounts.google.com" in page.url:
                        _emit("warning", "Still on Google login page, trying explicit GMC nav...", "redirect")
                        await page.goto("https://merchants.google.com/mc/setup", wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(3)
                    await _dismiss_overlays(page)
                    continue
                elif "accounts.google.com" not in page.url:
                    # Might have succeeded with redirect
                    _emit("info", "Login might have succeeded (redirect detected), resuming...", "login_detect")
                    continue
                else:
                    _emit("error", "Google login failed", "login_detect")
                    return {"success": False, "message": "Google login failed at step " + str(step_num), "steps": step_num}

            # Dump page DOM for AI
            dom_text = await _dump_dom_json(page, log_callback)
            page_title = await page.title()

            # Build prompt and get AI decision
            prompt = _build_ai_prompt(dom_text, site_url, business_info or {}, feed_url, completed_steps, page_title)

            try:
                action = await _call_deepseek_for_action(prompt, log_callback)
            except Exception as e:
                _emit("error", f"DeepSeek error: {e}", "ai_error")
                return {"success": False, "message": f"AI error at step {step_num}: {e}", "steps": step_num}

            act_type = action.get("action", "done")
            last_action = act_type + ":" + action.get("selector", "")
            _emit("info", f"Action: {act_type} | {str(action.get('reasoning',''))[:120]}", "ai_decision")

            if act_type == "done":
                mc_account_id = await _extract_mc_id(page) or "registered"
                _emit("info", f"Registration complete! MC ID: {mc_account_id}", "done")
                completed_steps.append(f"step{step_num}:done")
                break

            if act_type == "fail":
                reason = action.get("reasoning", "Unknown error")
                _emit("error", f"AI reports failure: {reason}", "ai_fail")
                return {"success": False, "message": f"Failed at step {step_num}: {reason}", "steps": step_num}

            # Execute action
            try:
                await _execute_action(page, action, log_callback)
                completed_steps.append(f"step{step_num}:{act_type}")
            except Exception as e:
                _emit("warning", f"Action error: {e}", "action_error")
                completed_steps.append(f"step{step_num}:error:{str(e)[:40]}")

        if step_num >= MAX_STEPS:
            _emit("warning", f"Reached max steps ({MAX_STEPS})", "max_steps")

        return {
            "success": bool(mc_account_id),
            "mc_account_id": mc_account_id,
            "message": f"GMC registration {'complete' if mc_account_id else 'incomplete'} ({len(completed_steps)} steps)",
            "steps": len(completed_steps),
        }

    finally:
        try:
            if context:
                await context.close()
        except Exception:
            pass




def country_to_locale(country: str) -> str:
    """国家代码 → BCP 47 locale。"""
    return _REGION_CONFIGS.get(country, _REGION_CONFIGS["US"])["locale"]

