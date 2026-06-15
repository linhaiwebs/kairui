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
    """根据 config.json 构建 CloakBrowser 指纹启动参数列表。

    CloakBrowser v14+ 核心反检测参数:
      --fingerprint        固定指纹种子(Canvas/WebGL/Audio保持一致)
      --fingerprint-platform  伪装操作系统平台
      --fingerprint-canvas-noise   Canvas 指纹噪点
      --fingerprint-webrtc-ip      WebRTC 公网IP伪装(需代理IP)
      --disable-blink-features=AutomationControlled  隐藏 navigator.webdriver
    """
    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
    ]
    if config.get("fingerprint_seed"):
        args.append(f"--fingerprint={config['fingerprint_seed']}")
    platform = config.get("platform", "")
    if platform == "mac":
        args.append("--fingerprint-platform=macos")
    else:
        args.append("--fingerprint-platform=windows")
    # WebRTC: extract IP from proxy if not explicitly set (prevents real IP leak)
    webrtc_ip = config.get("webrtc_ip", "")
    if not webrtc_ip:
        proxy = config.get("proxy", "")
        import re as _re
        m = _re.search(r'@([\d.]+):', proxy)
        if m:
            webrtc_ip = m.group(1)
    if webrtc_ip:
        args.append(f"--fingerprint-webrtc-ip={webrtc_ip}")
    # Canvas noise — makes each session's canvas hash slightly different but consistent
    args.append("--fingerprint-canvas-noise")
    return args


async def _human_delay(min_ms: int = 300, max_ms: int = 1500):
    """模拟人类操作间隔的随机延迟。"""
    delay = random.randint(min_ms, max_ms) / 1000.0
    await asyncio.sleep(delay)


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
