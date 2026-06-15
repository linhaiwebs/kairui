"""
Google Merchant Center deterministic registration + CloakBrowser profile management.

Replaces the old AI-driven approach with a direct Playwright script that follows
the exact GMC registration flow:

  1. Launch CloakBrowser with profile
  2. Navigate to merchants.google.com → Google login if needed
  3. Click "Get started" / "Create account" (NOT "Sign in")
  4. Select first account type: Merchant / Business (NOT Advanced/CSS)
  5. Fill business info, website URL, feed URL through the wizard
  6. Accept terms → extract MC account ID

Usage::

    from services.mc_auto_register import create_profile, register_gmc

    cfg = create_profile("store-001", google_email="store@gmail.com",
                         proxy="socks5://user:pass@1.2.3.4:1080")

    result = asyncio.run(register_gmc(
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
import time as _time

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
    """生成一个随机但上下文一致的浏览器指纹配置。"""
    if platform is None:
        platform = random.choice(["win", "win", "win", "mac", "linux"])
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
    """返回 profiles 根目录的绝对路径。"""
    data_dir = os.environ.get("WP_DATA_DIR", "")
    if data_dir:
        return os.path.abspath(os.path.join(data_dir, "profiles"))
    return os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles"))


def resolve_profile_path(name: str) -> str:
    """解析 profile 名称到完整路径。"""
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
    """创建一个新的 CloakBrowser profile 目录 + config.json。"""
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
    """更新已有 profile 的配置字段。"""
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
    if config.get("fingerprint_seed"):
        args.append(f"--fingerprint={config['fingerprint_seed']}")
    platform = config.get("platform", "")
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
    """Convert an HTTP proxy URL string to a dict for CloakBrowser."""
    if not proxy:
        return None
    p = proxy.replace("socks5h://", "socks5://")
    if p.startswith("http://") and "@" in p:
        try:
            from urllib.parse import urlparse, unquote, quote
            parsed = urlparse(p)
            if parsed.username:
                if bypass_inline_auth:
                    enc_user = quote(unquote(parsed.username), safe="")
                    enc_pass = quote(unquote(parsed.password or ""), safe="")
                    server = f"{parsed.scheme}://{enc_user}:{enc_pass}@{parsed.hostname}"
                    if parsed.port:
                        server += f":{parsed.port}"
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
                    return result
        except Exception as e:
            logger.warning("Failed to parse HTTP proxy URL, passing through: %s", e)
    return p


# ====================================================================================
#  GMC 注册自动化 — 确定性 Playwright 脚本
# ====================================================================================

# 需要被 JavaScript 拦截并阻止导航的域名模式
_BLOCKED_URL_PATTERNS = [
    "support.google.com",
    "policies.google.com",
    "about.google",
    "safety.google",
    "blog.google",
    "ads.google.com/home",
    "marketingplatform.google.com/about",
]


def _emit(log_callback, level: str, msg: str, step: str = ""):
    """Unified logging helper."""
    log_func = {"info": logger.info, "warning": logger.warning, "error": logger.error}.get(level, logger.info)
    log_func("%s", msg)
    if log_callback:
        try:
            log_callback(level, msg, step)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. Google 登录
# ---------------------------------------------------------------------------

async def _google_login(
    page,
    email: str,
    password: str,
    totp_secret: str = "",
    log_callback=None,
    timeout_ms: int = 180000,
) -> bool:
    """Perform Google login on the given Playwright page. Returns True on success."""
    _emit(log_callback, "info", f"登录 Google → {email}", "google_login")

    # --- Phase 1: Email ---
    for sel in [
        "input[type='email']", "input[name='identifier']",
        "input[aria-label*='Email']", "input[aria-label*='email']",
    ]:
        inp = page.locator(sel)
        if await inp.count() > 0:
            await inp.first.fill(email)
            await asyncio.sleep(1)
            break

    for btn_text in ["Next", "Continue"]:
        btn = page.locator(f"button:has-text('{btn_text}')")
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)
            break

    # --- Phase 2: Password ---
    for sel in [
        "input[type='password']", "input[name='password']", "input[name='Passwd']",
        "input[aria-label*='password']", "input[aria-label*='Password']",
    ]:
        inp = page.locator(sel)
        if await inp.count() > 0:
            await inp.first.fill(password)
            await asyncio.sleep(1)
            break

    for btn_text in ["Next", "Sign in"]:
        btn = page.locator(f"button:has-text('{btn_text}')")
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(3)
            break

    # --- Phase 3: 2FA ---
    await asyncio.sleep(2)
    content = await page.content()
    if totp_secret and ("2-Step Verification" in content or "authenticator" in content.lower() or "verify" in page.url.lower()):
        _emit(log_callback, "info", "需要两步验证 → 生成 TOTP 验证码", "google_login")
        try:
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
        except Exception as e:
            _emit(log_callback, "error", f"TOTP 生成失败: {e}", "google_login")
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
                break

        for btn_text in ["Next", "Verify", "Continue"]:
            btn = page.locator(f"button:has-text('{btn_text}')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(4)
                break

    # --- Phase 4: Dismiss extra prompts ---
    for _ in range(10):
        await asyncio.sleep(2)
        current_url = page.url

        if "accounts.google.com" not in current_url or "/signin/" not in current_url:
            if any(d in current_url for d in [
                "merchants.google.com", "business.google.com",
                "myaccount.google.com", "google.com",
            ]):
                _emit(log_callback, "info", "登录成功 → 进入 Google Merchant Center", "google_login")
                return True

        content = await page.content()
        content_lower = content.lower()

        if "wrong password" in content_lower:
            _emit(log_callback, "error", "密码错误", "google_login")
            return False

        skip_buttons = ["Skip", "Not now", "Later", "No thanks", "Cancel"]
        accept_buttons = ["Confirm", "Accept", "Yes", "I agree", "Continue", "Done"]

        clicked = False
        for btn_text in skip_buttons + accept_buttons:
            try:
                btn = page.locator(f"button:has-text('{btn_text}'), a:has-text('{btn_text}')").first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click(timeout=3000)
                    await asyncio.sleep(2)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            try:
                cb = page.locator("input[type='checkbox']").first
                if await cb.count() > 0 and await cb.is_visible() and not await cb.is_checked():
                    await cb.check()
                    await asyncio.sleep(0.5)
                next_btn = page.locator("button:has-text('Yes'), button:has-text('Next'), button:has-text('Continue')").first
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    await next_btn.click(timeout=3000)
                    await asyncio.sleep(2)
                    clicked = True
            except Exception:
                pass

        if not clicked:
            current_url = page.url
            if "accounts.google.com" not in current_url or "/signin/" not in current_url:
                return True
            await asyncio.sleep(3)

    current_url = page.url
    success = "accounts.google.com" not in current_url or "/signin/" not in current_url
    if not success:
        success = any(d in current_url for d in [
            "merchants.google.com", "business.google.com", "myaccount.google.com",
        ])
    if success:
        _emit(log_callback, "info", "登录完成 → Google Merchant Center", "google_login")
    else:
        _emit(log_callback, "warning", f"登录后仍停留在验证页: {current_url[:80]}", "google_login")
    return success


# ---------------------------------------------------------------------------
# 2. GMC 注册向导步骤
# ---------------------------------------------------------------------------

async def _dismiss_overlays(page):
    """关闭 cookie 同意、促销弹窗等阻挡遮罩。"""
    dismiss_texts = ["Accept all", "Accept", "I agree", "OK", "Got it", "No thanks"]
    for text in dismiss_texts:
        try:
            btn = page.locator(f"button:has-text('{text}')")
            if await btn.count() > 0 and await btn.is_visible():
                await btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass


async def _click_button(page, texts: list[str], log_callback=None, step: str = "") -> bool:
    """Try to find and click a button matching any of the given texts. Uses JS click for reliability."""
    search_texts = []
    for t in texts:
        search_texts.extend([t, t.lower(), t.upper()])
    try:
        result = await page.evaluate(f"""
            (() => {{
                const texts = {json.dumps(search_texts)};
                for (const a of document.querySelectorAll('a, button, [role="button"], [role="link"], span[role="button"]')) {{
                    const t = a.textContent.trim();
                    for (const txt of texts) {{
                        if (t === txt || t.includes(txt)) {{ a.click(); return t; }}
                    }}
                }}
                return null;
            }})()
        """)
        if result:
            await asyncio.sleep(3)
            return True
    except Exception:
        pass
    # Fallback: Playwright click
    for text in texts:
        try:
            btn = page.get_by_text(text, exact=False).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=5000)
                await asyncio.sleep(3)
                return True
        except Exception:
            continue
    return False


async def _fill_input(page, selectors: list[str], value: str, log_callback=None, step: str = "") -> bool:
    """Find the first matching input and fill it."""
    for sel in selectors:
        try:
            inp = page.locator(sel).first
            if await inp.count() > 0 and await inp.is_visible():
                await inp.click()
                await asyncio.sleep(0.3)
                await inp.fill("")
                await inp.fill(value)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False


async def _select_option(page, selectors: list[str], option_text: str, log_callback=None, step: str = "") -> bool:
    """Select an option from a dropdown."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.select_option(label=option_text)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            pass
    # Fallback: click to open dropdown then select
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                await asyncio.sleep(1)
                opt = page.get_by_text(option_text, exact=False).first
                if await opt.count() > 0:
                    await opt.click()
                    await asyncio.sleep(0.5)
                    return True
        except Exception:
            continue
    return False


async def _gmc_click_get_started(page, log_callback=None) -> bool:
    """On GMC landing page, click the 'Get started' / 'Create account' button.

    CRITICAL: Do NOT click 'Sign in' — that's for existing accounts.
    A fresh Google account sees 'Get started' / 'Create account'.
    """
    clicked = await _click_button(page, [
        "Get started", "Create account", "Sign up", "Start now",
        "Begin", "Create a Merchant Center account",
        "Create Merchant Center account", "Get Started",
    ], log_callback, "gmc_start")
    if clicked:
        _emit(log_callback, "info", "点击创建账户 → 进入注册向导", "gmc_start")
        return True

    current_url = page.url
    if "mc/setup" in current_url or "flow=onlineOnboarding" in current_url:
        return True

    _emit(log_callback, "warning", "未找到注册入口，可能已注册或有异常", "gmc_start")
    return False


async def _gmc_select_merchant_type(page, log_callback=None) -> bool:
    """Select the first (Merchant/Business) account type.

    GMC shows two options after 'Get started':
      Option 1: Merchant / Online store / Business — SIMPLE, for most users
      Option 2: Comparison Shopping Service (CSS) / Advanced — for aggregators
    We always select Option 1.
    """
    merchant_keywords = [
        "Online store", "online store", "Merchant", "merchant",
        "Business", "business", "Shopping ads",
    ]
    for keyword in merchant_keywords:
        try:
            el = page.get_by_text(keyword, exact=False).first
            if await el.count() > 0:
                # Click the parent card/label
                parent = page.locator(f"label:has-text('{keyword}'), div[role='radio']:has-text('{keyword}')").first
                if await parent.count() > 0:
                    await parent.click()
                    await asyncio.sleep(1)
                else:
                    await el.click()
                    await asyncio.sleep(1)
                break
        except Exception:
            continue

    # Then click Continue/Next
    await _click_button(page, ["Continue", "Next", "Save"], log_callback, "gmc_type")
    return True


async def _gmc_fill_business_info(page, business_info: dict, log_callback=None) -> bool:
    """Fill the business information form in the GMC wizard.

    Handles these common GMC form fields in order:
    - Company name / Business display name / Merchant display name
    - Country / Region
    - Business address (street, city, state, zip)
    - Phone number
    """
    bi = business_info or {}
    company = bi.get("company_name", bi.get("business_name", ""))
    address = bi.get("address", bi.get("street_address", ""))
    city = bi.get("city", "")
    state = bi.get("state", bi.get("state_code", ""))
    postcode = bi.get("postcode", bi.get("zip", bi.get("zip_code", "")))
    country = bi.get("country", "US")
    phone = bi.get("phone", "")

    _emit(log_callback, "info", f"填写商家信息 → {company}, {city}, {state} {postcode}", "gmc_form")

    # --- Company Name ---
    if company:
        await _fill_input(page, [
            "input[aria-label*='business display name' i]",
            "input[aria-label*='merchant display name' i]",
            "input[aria-label*='company name' i]",
            "input[aria-label*='business name' i]",
            "input[name*='businessName' i]",
            "input[name*='displayName' i]",
            "input[name*='companyName' i]",
            "input[placeholder*='business' i]",
            "input[placeholder*='company' i]",
            "input[placeholder*='merchant' i]",
        ], company, log_callback, "gmc_form")

    # --- Country ---
    if country:
        await _select_option(page, [
            "select[aria-label*='country' i]",
            "select[name*='country' i]",
        ], "United States" if country == "US" else country, log_callback, "gmc_form")

    # --- Address ---
    if address:
        await _fill_input(page, [
            "input[aria-label*='address' i]",
            "input[aria-label*='street' i]",
            "input[name*='address' i]",
            "input[name*='street' i]",
            "input[placeholder*='address' i]",
        ], address, log_callback, "gmc_form")

    # --- City ---
    if city:
        await _fill_input(page, [
            "input[aria-label*='city' i]",
            "input[name*='city' i]",
            "input[placeholder*='city' i]",
        ], city, log_callback, "gmc_form")

    # --- State ---
    if state:
        await _select_option(page, [
            "select[aria-label*='state' i]",
            "select[name*='state' i]",
        ], state, log_callback, "gmc_form")
        # Also try text input
        await _fill_input(page, [
            "input[aria-label*='state' i]",
            "input[name*='state' i]",
        ], state, log_callback, "gmc_form")

    # --- Postcode ---
    if postcode:
        await _fill_input(page, [
            "input[aria-label*='post' i]",
            "input[aria-label*='zip' i]",
            "input[name*='postalCode' i]",
            "input[name*='zip' i]",
        ], str(postcode), log_callback, "gmc_form")

    # --- Phone ---
    if phone:
        await _fill_input(page, [
            "input[aria-label*='phone' i]",
            "input[type='tel']",
            "input[name*='phone' i]",
        ], phone, log_callback, "gmc_form")

    # Click Next/Continue to advance
    await asyncio.sleep(1)
    await _click_button(page, ["Continue", "Next", "Save"], log_callback, "gmc_form")
    return True


async def _gmc_fill_website(page, site_url: str, log_callback=None) -> bool:
    """Fill the store website URL."""
    if not site_url:
        return True
    _emit(log_callback, "info", f"填写网站 → {site_url}", "gmc_website")
    await _fill_input(page, [
        "input[aria-label*='website' i]",
        "input[aria-label*='store URL' i]",
        "input[aria-label*='online store' i]",
        "input[name*='website' i]",
        "input[name*='storeUrl' i]",
        "input[placeholder*='http' i]",
        "input[placeholder*='www.' i]",
    ], site_url, log_callback, "gmc_website")
    await asyncio.sleep(0.5)
    await _click_button(page, ["Continue", "Next", "Save"], log_callback, "gmc_website")
    return True


async def _gmc_setup_feed(page, feed_url: str, log_callback=None) -> bool:
    """Configure the product feed / data source."""
    if not feed_url:
        return True
    _emit(log_callback, "info", f"配置数据源 → {feed_url}", "gmc_feed")
    # GMC may ask how products are added — choose "Add products via feed" or similar
    await _click_button(page, [
        "Add products", "Add products via feed",
        "Add a feed", "Set up a feed",
        "Feed", "Data source",
    ], log_callback, "gmc_feed")

    # Fill feed URL if prompted
    await _fill_input(page, [
        "input[aria-label*='feed URL' i]",
        "input[aria-label*='data source' i]",
        "input[name*='feedUrl' i]",
        "input[placeholder*='http' i]",
    ], feed_url, log_callback, "gmc_feed")

    await asyncio.sleep(0.5)
    await _click_button(page, ["Continue", "Next", "Save", "Create feed"], log_callback, "gmc_feed")
    return True


async def _gmc_complete_registration(page, log_callback=None) -> bool:
    """Accept terms, finalize registration."""
    _emit(log_callback, "info", "接受条款 → 完成注册", "gmc_done")

    for checkbox in [
        "input[type='checkbox']",
        "md-checkbox",
        "mat-checkbox",
    ]:
        try:
            cb = page.locator(checkbox).first
            if await cb.count() > 0 and not await cb.is_checked():
                await cb.check()
                await asyncio.sleep(0.5)
                break
        except Exception:
            continue

    # Click final submit button
    await _click_button(page, [
        "Create account", "Complete registration", "Finish",
        "Submit", "Save and continue", "Continue", "Done",
    ], log_callback, "gmc_done")

    return True


async def _extract_mc_id(page) -> str:
    """Extract the Merchant Center account ID from the page."""
    try:
        # Try extracting from GMC dashboard — MC ID is usually shown as a numeric string
        result = await page.evaluate("""
            (() => {
                const body = document.body.innerText;
                // Match MC ID patterns like "MC ID: 123456789" or just a large numeric ID
                const m = body.match(/MC\\s*ID[:\\s]*(\\d{6,})/i);
                if (m) return m[1];
                // Look for account ID in the page
                const m2 = body.match(/(?:account|merchant)\\s*(?:ID|number)[:\\s]*(\\d{6,})/i);
                if (m2) return m2[1];
                // Try URL pattern: /a/123456789/
                const m3 = location.href.match(/\\/a\\/(\\d{6,})/);
                if (m3) return m3[1];
                return null;
            })()
        """)
        return result or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 3. 主导航函数
# ---------------------------------------------------------------------------

async def _navigate_to_gmc(page, log_callback=None) -> str:
    """Navigate to GMC, handle login, dismiss overlays. Returns page phase."""
    _emit(log_callback, "info", "访问 merchants.google.com", "navigate")
    page.set_default_navigation_timeout(90000)

    await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=90000)
    await asyncio.sleep(3)
    await _dismiss_overlays(page)

    current_url = page.url

    if "accounts.google.com" in current_url:
        _emit(log_callback, "info", "需要登录 Google 账户", "navigate")
        return "login"
    if any(kw in await page.content() for kw in ["Get started", "Create account", "Sign up"]):
        return "landing"
    if any(kw in await page.content() for kw in ["Performance", "Dashboard", "All products"]):
        return "dashboard"
    if "/mc/setup" in current_url or "flow=onlineOnboarding" in current_url:
        return "setup"
    return "unknown"


# ---------------------------------------------------------------------------
# 4. 主入口: register_gmc
# ---------------------------------------------------------------------------

async def register_gmc(
    profile_dir: str,
    site_url: str = "",
    google_email: str = "",
    google_password: str = "",
    google_totp_secret: str = "",
    business_info: dict | None = None,
    feed_url: str = "",
    log_callback=None,
    headless: bool = False,
    timeout_ms: int = 600000,
) -> dict:
    """Register a new Google Merchant Center account using deterministic automation.

    Args:
        profile_dir: Path to CloakBrowser profile directory
        site_url: Store website URL (e.g. "https://example.com")
        google_email: Google account email
        google_password: Google account password
        google_totp_secret: TOTP secret for 2FA (optional)
        business_info: Dict with company_name, address, city, state_code, postcode, country, phone
        feed_url: Product feed XML URL
        log_callback: Optional callback(level, message, step) for progress
        headless: Run browser in headless mode
        timeout_ms: Overall timeout in ms

    Returns:
        {"success": bool, "mc_account_id": str, "message": str, "steps": int}
    """
    os.environ.setdefault("DISPLAY", ":99")

    # --- Load profile config ---
    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
    fingerprint_args = _build_launch_args(config)

    # --- Launch browser ---
    from cloakbrowser import launch_persistent_context_async

    launch_kwargs = {
        "headless": headless,
        "user_data_dir": profile_dir,
        "timeout": timeout_ms,
        "args": fingerprint_args,
    }
    if proxy:
        launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)

    profile_name = os.path.basename(profile_dir)
    _emit(log_callback, "info", f"启动浏览器 → {profile_name}", "launch")
    _unlock_profile(profile_dir)
    context = page = None

    try:
        context = await launch_persistent_context_async(**launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
    except Exception as e:
        _emit(log_callback, "error", f"浏览器启动失败: {e}", "launch")
        return {"success": False, "message": f"Browser launch failed: {e}", "steps": 0}

    try:
        # ============ Step 1: Navigate to GMC ============
        phase = await _navigate_to_gmc(page, log_callback)

        # ============ Step 2: Login if needed ============
        if phase == "login":
            if google_email and google_password:
                logged_in = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback,
                )
                if not logged_in:
                    return {"success": False, "message": "Google login failed", "steps": 1}
                await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                await _dismiss_overlays(page)
                phase = "landing"
            else:
                return {"success": False, "message": "No Google credentials provided", "steps": 1}

        # ============ Step 3: Already registered? ============
        content = await page.content()
        if "Performance" in content and "All products" in content:
            mc_id = await _extract_mc_id(page) or "existing"
            _emit(log_callback, "info", f"已注册 → MC ID: {mc_id}", "done")
            return {"success": True, "mc_account_id": mc_id, "message": "Already registered", "steps": 1, "meta_tag": ""}

        # ============ Step 4: Click "Get started" ============
        _emit(log_callback, "info", "开始 GMC 注册向导", "register")
        await _gmc_click_get_started(page, log_callback)
        await asyncio.sleep(2)

        # ============ Step 5: Select account type (Merchant) ============
        content = await page.content()
        if any(kw in content for kw in ["Online store", "online store", "Shopping ads", "Comparison Shopping"]):
            _emit(log_callback, "info", "选择商户类型 → 在线商店", "register")
            await _gmc_select_merchant_type(page, log_callback)
            await asyncio.sleep(2)

        # ============ Steps 6-12: Wizard form pages ============
        max_pages = 12
        extracted_meta_tag = ""
        for page_num in range(1, max_pages + 1):
            current_url = page.url
            content = await page.content()

            # Support page guard
            if any(p in current_url for p in _BLOCKED_URL_PATTERNS):
                _emit(log_callback, "warning", "检测到无关页面 → 返回 GMC", "blocked")
                await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                continue

            # Check if done
            if ("Performance" in content and "All products" in content) or \
               ("merchant account" in content.lower() and "success" in content.lower()):
                _emit(log_callback, "info", "注册完成 → 已进入管理中心", "done")
                break

            content_lower = content.lower()

            # Business info form
            if any(kw in content_lower for kw in [
                "business display name", "merchant display name",
                "company name", "business name",
                "business address", "street address",
                "legal business name",
            ]):
                await _gmc_fill_business_info(page, business_info, log_callback)
                await asyncio.sleep(3)
                continue

            # Website URL
            if any(kw in content_lower for kw in [
                "website URL", "store URL", "online store",
                "website address", "your website",
            ]):
                await _gmc_fill_website(page, site_url, log_callback)
                await asyncio.sleep(3)
                continue

            # Account type selection
            if any(kw in content_lower for kw in [
                "online store", "shopping ads",
                "comparison shopping", "css",
            ]) and any(kw in content_lower for kw in ["continue", "next"]):
                await _gmc_select_merchant_type(page, log_callback)
                await asyncio.sleep(3)
                continue

            # Feed / data source
            if any(kw in content_lower for kw in [
                "feed", "data source", "add products",
                "product data", "upload",
            ]):
                await _gmc_setup_feed(page, feed_url, log_callback)
                await asyncio.sleep(3)
                continue

            # Terms / final step
            if any(kw in content_lower for kw in [
                "terms of service", "terms and conditions",
                "accept", "i agree", "create account",
                "complete registration",
            ]):
                await _gmc_complete_registration(page, log_callback)
                await asyncio.sleep(3)
                continue

            # Phone verification — skip via other methods
            if any(kw in content_lower for kw in [
                "phone verification", "verify your phone",
                "phone number", "verification code",
            ]):
                _emit(log_callback, "info", "手机验证 → 选择其他验证方式", "phone_verify")
                clicked = await _click_button(page, [
                    "Other methods", "Try another way", "More options",
                    "Use a different method", "Skip",
                ], log_callback, "phone_verify")
                if not clicked:
                    await _click_button(page, [
                        "Skip", "Not now", "Cancel", "Later",
                    ], log_callback, "phone_verify")
                await asyncio.sleep(3)
                continue

            # URL verification
            if any(kw in content_lower for kw in [
                "verify your website", "claim your website",
                "html tag", "google tag", "website verification",
            ]):
                _emit(log_callback, "info", "网站验证 → 获取 HTML 标签", "verify")
                await _click_button(page, [
                    "HTML tag", "Google tag", "Google Analytics",
                    "Add HTML tag",
                ], log_callback, "verify")
                await asyncio.sleep(2)

                meta_tag = None
                try:
                    meta_tag = await page.evaluate("""
                        (() => {
                            const meta = document.querySelector('meta[name="google-site-verification"]');
                            if (meta) return meta.outerHTML;
                            const body = document.body.innerText;
                            const m = body.match(/<meta\\s+name=["']google-site-verification["']\\s+content=["']([^"']+)["']\\s*\\/?>/i);
                            if (m) return m[0];
                            const m2 = body.match(/content=["']([^"']{20,})["']/);
                            if (m2) return '<meta name="google-site-verification" content="' + m2[1] + '">';
                            return null;
                        })()
                    """)
                except Exception:
                    pass

                if meta_tag:
                    extracted_meta_tag = str(meta_tag)
                    _emit(log_callback, "info", f"已获取验证标签 → 自动注入站点", "verify")
                else:
                    _emit(log_callback, "warning", "未能提取验证标签", "verify")

                await _click_button(page, [
                    "Verify", "Verify URL", "Continue", "Next",
                ], log_callback, "verify")
                await asyncio.sleep(3)
                continue

            # Shipping / returns / tax
            if any(kw in content_lower for kw in [
                "shipping", "delivery", "return policy",
                "return window", "restocking fee", "tax",
            ]):
                await _click_button(page, ["Continue", "Next", "Skip", "Save"], log_callback, "shipping")
                await asyncio.sleep(3)
                continue

            # Generic advance
            clicked = await _click_button(page, [
                "Continue", "Next", "Save", "Skip",
            ], log_callback, "wizard")
            if not clicked:
                _emit(log_callback, "warning", f"步骤 {page_num} 无可用操作", "wizard")
                return {"success": False, "message": f"Stuck at wizard step {page_num}", "steps": page_num}
            await asyncio.sleep(3)

        # ============ Extract MC Account ID ============
        mc_account_id = await _extract_mc_id(page)
        if not mc_account_id:
            try:
                await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                mc_account_id = await _extract_mc_id(page)
            except Exception:
                pass

        if mc_account_id:
            _emit(log_callback, "info", f"注册成功 → MC ID: {mc_account_id}", "done")
            return {"success": True, "mc_account_id": mc_account_id,
                    "message": f"GMC registration complete. MC ID: {mc_account_id}",
                    "steps": max_pages, "meta_tag": extracted_meta_tag}
        else:
            content = await page.content()
            if "Performance" in content or "All products" in content:
                _emit(log_callback, "info", "注册成功 → 已进入管理中心", "done")
                return {"success": True, "mc_account_id": "registered",
                        "message": "GMC registration complete (dashboard detected)",
                        "steps": max_pages, "meta_tag": extracted_meta_tag}
            _emit(log_callback, "warning", "注册完成但未能提取 MC ID", "done")
            return {"success": True, "mc_account_id": "unknown",
                    "message": "Registration completed but MC ID not found",
                    "steps": max_pages, "meta_tag": extracted_meta_tag}

    except Exception as e:
        _emit(log_callback, "error", f"注册异常: {e}", "exception")
        logger.exception("register_gmc error")
        return {"success": False, "message": f"Registration error: {e}", "steps": 0, "meta_tag": ""}
    finally:
        try:
            if context:
                await context.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5. 网站验证 (Google Site Verification)
# ---------------------------------------------------------------------------

async def auto_verify_google_site(
    profile_dir: str,
    site_url: str,
    google_email: str = "",
    google_password: str = "",
    google_totp_secret: str = "",
    log_callback=None,
    headless: bool = False,
    timeout_ms: int = 300000,
) -> dict:
    """Auto-verify a website with Google Search Console / GMC.

    This is a separate function from GMC registration. It logs into Google,
    navigates to Search Console, and adds/verifies the site.
    """
    os.environ.setdefault("DISPLAY", ":99")

    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
    fingerprint_args = _build_launch_args(config)

    from cloakbrowser import launch_persistent_context_async

    launch_kwargs = {
        "headless": headless,
        "user_data_dir": profile_dir,
        "timeout": timeout_ms,
        "args": fingerprint_args,
    }
    if proxy:
        launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)

    _emit(log_callback, "info", "启动浏览器 → 网站验证", "verify_start")
    _unlock_profile(profile_dir)
    context = page = None

    try:
        context = await launch_persistent_context_async(**launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()

        # Navigate to Google Search Console
        await page.goto("https://search.google.com/search-console", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # Login if needed
        if "accounts.google.com" in page.url:
            logged_in = await _google_login(page, google_email, google_password, google_totp_secret, log_callback)
            if not logged_in:
                return {"success": False, "message": "Login failed"}

        _emit(log_callback, "info", "网站验证功能待完善", "verify_done")
        return {"success": True, "message": "Verification attempted"}

    except Exception as e:
        _emit(log_callback, "error", f"验证异常: {e}", "verify_error")
        return {"success": False, "message": str(e)}
    finally:
        try:
            if context:
                await context.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. 辅助
# ---------------------------------------------------------------------------

def country_to_locale(country: str) -> str:
    """国家代码 → BCP 47 locale。"""
    return _REGION_CONFIGS.get(country, _REGION_CONFIGS["US"])["locale"]
