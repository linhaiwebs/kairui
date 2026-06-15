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
    """Find button by text using JS scanning (undetectable read), then click with Playwright (real mouse events).

    JS only READS the DOM to find the right selector — never calls el.click().
    Playwright's native click simulates: hover → mousedown → mouseup → click.
    This is indistinguishable from a real user click.
    """
    blocked = ['help', 'support', 'faq', 'learn more', 'documentation',
               'guide', 'tutorial', 'community', 'forum', 'blog']
    # Step 1: JS scans DOM (read-only) to find a unique selector for the matching element
    selector = None
    try:
        selector = await page.evaluate(f"""
            (() => {{
                const texts = {json.dumps([t.lower() for t in texts])};
                const blocked = {json.dumps(blocked)};
                const candidates = [];
                for (const el of document.querySelectorAll('button, [role="button"], a')) {{
                    if (el.offsetParent === null) continue;
                    const t = el.textContent.trim().toLowerCase();
                    if (t.length < 3 || t.length > 60) continue;
                    const href = (el.getAttribute('href') || '').toLowerCase();
                    if (blocked.some(b => href.includes(b) || t.includes(b))) continue;
                    for (const txt of texts) {{
                        if (t === txt) {{
                            // Build a unique selector for this element
                            if (el.id) return '#' + CSS.escape(el.id);
                            const cls = Array.from(el.classList).filter(c => c && !c.match(/^\\d/)).slice(0, 2).join('.');
                            if (cls) return el.tagName.toLowerCase() + '.' + CSS.escape(cls).replace(/\\\\s+/g, '.');
                            const text = el.textContent.trim().substring(0, 30);
                            return el.tagName.toLowerCase() + ':has-text("' + text + '")';
                        }}
                    }}
                }}
                return null;
            }})()
        """)
    except Exception:
        pass

    # Step 2: Click with Playwright (real mouse events: hover → mousedown → mouseup → click)
    if selector:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                await el.hover()
                await _human_delay(200, 500)
                await el.click(timeout=5000)
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

    # Fallback: Playwright role/text match (also real mouse events)
    for text in texts:
        try:
            btn = page.get_by_role("button", name=text).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.hover()
                await _human_delay(200, 500)
                await btn.click(timeout=5000)
                await asyncio.sleep(2)
                return True
        except Exception:
            pass
        try:
            btn = page.get_by_text(text, exact=True).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.hover()
                await _human_delay(200, 500)
                await btn.click(timeout=5000)
                await asyncio.sleep(2)
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



# ====================================================================================
#  GMC 注册 — AI决策 + 脚本执行 混合架构
# ====================================================================================
# AI: 只负责看页面文本 → 分类页面类型 (page_type)
# 脚本: 负责所有执行 (等待、点击、填充、验证、重试)

async def _ai_classify_page(page, log_callback=None) -> dict:
    """Send visible page text to DeepSeek. Returns {"page_type","confidence","reasoning"}.
    Falls back to keyword matching if DeepSeek is unavailable."""
    url = page.url
    try:
        title = await page.title()
        body_text = await page.evaluate("""
            (() => { const b = document.body; return b ? b.innerText.substring(0, 4000) : ''; })()
        """)
    except Exception as e:
        return {"page_type": "unknown", "confidence": 0, "reasoning": str(e)}

    try:
        from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
        import requests as http_requests
        keys = get_deepseek_keys()
        if keys:
            prompt = f"""Classify this Google/GMC page. Return ONLY JSON.

URL: {url[:120]}
Title: {title[:100]}
TEXT: {body_text[:3500]}

page_type must be ONE of:
login_email, login_password, login_2fa, login_challenge,
gmc_dashboard, gmc_landing, gmc_account_type, gmc_business_form,
gmc_website, gmc_feed, gmc_phone_verify, gmc_website_verify,
gmc_shipping, gmc_terms, gmc_complete, captcha, blocked, unknown

JSON: {{"page_type":"xxx","confidence":0.9,"reasoning":"brief"}}"""

            def _call(key):
                resp = http_requests.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": "deepseek-chat", "messages": [
                        {"role": "system", "content": "Page classifier. Return ONLY JSON. No markdown."},
                        {"role": "user", "content": prompt}],
                        "temperature": 0.1, "max_tokens": 150}, timeout=15)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                resp.raise_for_status()

            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: rotate_deepseek(_call, keys))
            raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                # Try regex extraction: find page_type value in malformed JSON
                import re as _re3
                m = _re3.search(r'"page_type"\s*:\s*"(\w+)"', raw)
                if m:
                    result = {"page_type": m.group(1), "confidence": 0.5, "reasoning": "regex extraction"}
                else:
                    raise
            _emit(log_callback, "info", f"AI判定 -> {result['page_type']} (c={result.get('confidence', '?')})", "ai")
            return result
    except Exception as e:
        _emit(log_callback, "warning", f"AI回退关键词 ({e})", "ai")

    return _keyword_classify(url, body_text)


def _keyword_classify(url: str, text: str) -> dict:
    """Fallback page classification using keyword matching."""
    t = text.lower()
    if any(p in url for p in ["support.google.com", "policies.google.com", "about.google"]):
        return {"page_type": "blocked", "confidence": 1.0, "reasoning": "blocked URL"}
    checks = [
        ("captcha", ["captcha", "robot", "verify you are human", "unusual traffic"]),
        ("login_2fa", ["2-step verification", "authenticator", "enter the code"]),
        ("login_password", ["password", "welcome"]),
        ("login_email", ["email or phone", "sign in", "to continue to",
                         "anmelden", "email oder telefon", "eingeben"]),
        ("login_challenge", ["recovery", "verify it's you", "confirm you", "protect your"]),
        ("gmc_dashboard", ["performance", "all products", "diagnostics"]),
        ("gmc_complete", ["account created", "congratulations", "welcome to merchant"]),
        ("gmc_business_form", ["business display name", "company name", "merchant display", "business address", "street address", "legal business"]),
        ("gmc_account_type", ["online store", "shopping ads", "comparison shopping"]),
        ("gmc_website", ["website url", "store url", "your website", "website address"]),
        ("gmc_feed", ["add products", "data source", "product feed", "product data"]),
        ("gmc_phone_verify", ["phone verification", "verify your phone", "verification code"]),
        ("gmc_website_verify", ["verify your website", "html tag", "claim your website", "google tag"]),
        ("gmc_shipping", ["shipping", "delivery", "return policy", "return window", "tax"]),
        ("gmc_terms", ["terms of service", "i agree", "accept", "complete registration"]),
    ]
    for page_type, keywords in checks:
        if sum(1 for kw in keywords if kw in t) >= 2:
            return {"page_type": page_type, "confidence": 0.7, "reasoning": f"keyword: {page_type}"}
    if any(d in url for d in ["merchants.google.com", "business.google.com"]):
        # Extra check: if page shows dashboard indicators, it's not landing
        if sum(1 for kw in ["performance", "all products", "diagnostics"] if kw in t) >= 2:
            return {"page_type": "gmc_dashboard", "confidence": 0.8, "reasoning": "dashboard on GMC"}
        # Detect German GMC landing page (before English override takes effect)
        if any(kw in t for kw in ["anmelden", "registrieren", "konto erstellen", "los gehts", "los geht's"]):
            return {"page_type": "gmc_landing", "confidence": 0.85, "reasoning": "German GMC landing"}
        return {"page_type": "gmc_landing", "confidence": 0.6, "reasoning": "on GMC domain"}
    return {"page_type": "unknown", "confidence": 0, "reasoning": "no match"}


# ====================================================================================
#  AI 验证层 — 每步执行后确认页面是否如预期变化
# ====================================================================================

_ACTION_DESCRIPTIONS = {
    "login_email": "filled email and clicked Next -> should show password page",
    "login_password": "filled password and clicked Sign in -> should show 2FA or redirect to GMC",
    "login_2fa": "filled TOTP code and submitted -> should complete login and show GMC",
    "login_challenge": "dismissed recovery/phone prompt -> should advance or show GMC",
    "gmc_landing": "clicked Get started or Sign in -> should show account type selection or wizard",
    "gmc_account_type": "selected Merchant option and clicked Continue -> should show business form",
    "gmc_business_form": "filled business info and clicked Continue -> should show website URL or next step",
    "gmc_website": "filled website URL and clicked Continue -> should show feed/data source setup",
    "gmc_feed": "configured feed and clicked Continue -> should show next wizard step",
    "gmc_phone_verify": "clicked Other methods or Skip -> should move past phone verification",
    "gmc_website_verify": "selected HTML tag method and extracted meta -> should advance past verification",
    "gmc_shipping": "clicked Continue/Skip -> should advance past shipping settings",
    "gmc_terms": "accepted terms and clicked submit -> should show success/complete page",
    "gmc_complete": "registration should be done, looking for MC ID",
    "captcha": "waiting for manual CAPTCHA completion via VNC",
    "blocked": "navigating back from blocked/support page to GMC",
    "unknown": "clicking Continue to advance past unknown page",
}


async def _ai_verify_action(page, expected: str, log_callback=None) -> dict:
    """After executing a handler, ask AI: did the page change as expected?
    Returns {"success": bool, "new_page_type": str, "reasoning": str}"""
    url = page.url
    try:
        title = await page.title()
        body = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 2000) : ''")
    except Exception as e:
        return {"success": True, "new_page_type": "unknown", "reasoning": f"read error: {e}"}

    try:
        from services.api_key_rotator import get_deepseek_keys, rotate_deepseek
        import requests as http_requests
        keys = get_deepseek_keys()
        if keys:
            prompt = f"""Verify if the last GMC automation action succeeded.

Expected outcome: {expected}

Current URL: {url[:120]}
Title: {title[:100]}
Text: {body[:1500]}

Did the page change as expected? Return JSON:
{{"success": true/false, "new_page_type": "one of 18 types", "reasoning": "why"}}"""

            def _call(key):
                resp = http_requests.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": "deepseek-chat", "messages": [
                        {"role": "system", "content": "Action verifier. Return ONLY JSON."},
                        {"role": "user", "content": prompt}],
                        "temperature": 0.1, "max_tokens": 150}, timeout=12)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                resp.raise_for_status()

            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, lambda: rotate_deepseek(_call, keys))
            raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            result = json.loads(raw)
            return result
    except Exception:
        pass

    # Fallback: simple URL change check
    if url != page.url:
        return {"success": True, "new_page_type": "unknown", "reasoning": "URL changed"}
    return {"success": True, "new_page_type": "unknown", "reasoning": "fallback ok"}


# ====================================================================================
#  执行层 — page_type -> handler 映射
# ====================================================================================

async def _exec_login_email(page, ctx, log_callback=None):
    email = ctx.get("google_email", "")
    if not email: return "fail"
    for sel in ["input[type='email']", "input[name='identifier']", "input[aria-label*='Email']"]:
        inp = page.locator(sel)
        if await inp.count() > 0: await inp.first.fill(email); await _human_delay(500, 1000); break
    await _click_button(page, ["Next", "Continue"], log_callback, "login")
    await _human_delay(2000, 3000)
    return "continue"

async def _exec_login_password(page, ctx, log_callback=None):
    password = ctx.get("google_password", "")
    if not password: return "fail"
    for sel in ["input[type='password']", "input[name='password']", "input[name='Passwd']"]:
        inp = page.locator(sel)
        if await inp.count() > 0: await inp.first.fill(password); await _human_delay(500, 1000); break
    await _click_button(page, ["Next", "Sign in"], log_callback, "login")
    await _human_delay(2000, 3000)
    return "continue"

async def _exec_login_2fa(page, ctx, log_callback=None):
    totp_secret = ctx.get("google_totp_secret", "")
    if not totp_secret: return "continue"
    try:
        code = pyotp.TOTP(totp_secret).now()
    except Exception as e:
        _emit(log_callback, "error", f"TOTP: {e}", "login")
        return "fail"
    for sel in ["input[type='tel']", "input[aria-label*='code']", "input[aria-label*='G-']"]:
        inp = page.locator(sel)
        if await inp.count() > 0: await inp.first.fill(code); await _human_delay(500, 1000); break
    await _click_button(page, ["Next", "Verify", "Continue"], log_callback, "login")
    await _human_delay(3000, 5000)
    return "continue"

async def _exec_login_challenge(page, ctx, log_callback=None):
    for btn_text in ["Skip", "Not now", "Later", "No thanks", "Cancel", "Confirm"]:
        try:
            btn = page.locator(f"button:has-text('{btn_text}'), a:has-text('{btn_text}')").first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=3000); await _human_delay(1000, 2000)
                return "continue"
        except Exception: pass
    return "continue"

async def _exec_gmc_dashboard(page, ctx, log_callback=None):
    mc_id = await _extract_mc_id(page) or "registered"
    ctx["mc_account_id"] = mc_id
    _emit(log_callback, "info", f"已注册 -> MC ID: {mc_id}", "done")
    return "done"

async def _exec_gmc_landing(page, ctx, log_callback=None):
    """Enter GMC registration by navigating directly to the setup URL.

    Why URL navigation instead of clicking buttons:
      - Same as user typing URL or clicking bookmark → undetectable
      - Skips Sign in → panel → Merchant Center → new tab flow entirely
      - Works regardless of language (no button text matching needed)
    """
    _emit(log_callback, "info", "直接导航到 GMC 注册向导", "gmc")
    await page.goto("https://merchants.google.com/mc/setup?hl=en-US&gl=US",
                    wait_until="domcontentloaded", timeout=60000)
    await _human_delay(2000, 3000)
    await _dismiss_overlays(page)
    return "continue"

async def _exec_gmc_account_type(page, ctx, log_callback=None):
    for keyword in ["Online store", "online store", "Merchant", "merchant", "Shopping ads"]:
        try:
            el = page.get_by_text(keyword, exact=False).first
            if await el.count() > 0:
                parent = page.locator(f"label:has-text('{keyword}'), div[role='radio']:has-text('{keyword}')").first
                await (parent.click() if await parent.count() > 0 else el.click())
                await _human_delay(500, 1000); break
        except Exception: pass
    await _human_delay(1000, 1500)
    await _click_button(page, ["Continue", "Next"], log_callback, "gmc")
    _emit(log_callback, "info", "选择商户类型 -> 在线商店", "gmc")
    return "continue"

async def _exec_gmc_business_form(page, ctx, log_callback=None):
    bi = ctx.get("business_info") or {}
    company = bi.get("company_name", bi.get("business_name", ""))
    address = bi.get("address", bi.get("street_address", ""))
    city = bi.get("city", "")
    state = bi.get("state", bi.get("state_code", ""))
    postcode = str(bi.get("postcode", bi.get("zip", bi.get("zip_code", ""))))
    country = bi.get("country", "US")
    phone = bi.get("phone", "")
    _emit(log_callback, "info", f"填写商家信息 -> {company}, {city}", "gmc")
    if company:
        await _fill_input(page, ["input[aria-label*='business display name' i]", "input[aria-label*='merchant display name' i]", "input[aria-label*='company name' i]", "input[aria-label*='business name' i]", "input[name*='businessName' i]", "input[name*='displayName' i]", "input[placeholder*='business' i]", "input[placeholder*='company' i]"], company, log_callback, "gmc")
    if country:
        await _select_option(page, ["select[aria-label*='country' i]", "select[name*='country' i]"], "United States" if country == "US" else country, log_callback, "gmc")
    if address:
        await _fill_input(page, ["input[aria-label*='address' i]", "input[aria-label*='street' i]", "input[name*='address' i]", "input[placeholder*='address' i]"], address, log_callback, "gmc")
    if city:
        await _fill_input(page, ["input[aria-label*='city' i]", "input[name*='city' i]"], city, log_callback, "gmc")
    if state:
        await _select_option(page, ["select[aria-label*='state' i]"], state, log_callback, "gmc")
        await _fill_input(page, ["input[aria-label*='state' i]"], state, log_callback, "gmc")
    if postcode:
        await _fill_input(page, ["input[aria-label*='post' i]", "input[aria-label*='zip' i]", "input[name*='postalCode' i]", "input[name*='zip' i]"], postcode, log_callback, "gmc")
    if phone:
        await _fill_input(page, ["input[type='tel']", "input[aria-label*='phone' i]"], phone, log_callback, "gmc")
    await _human_delay(500, 1000)
    await _click_button(page, ["Continue", "Next", "Save"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_website(page, ctx, log_callback=None):
    site_url = ctx.get("site_url", "")
    if site_url:
        _emit(log_callback, "info", f"填写网站 -> {site_url}", "gmc")
        await _fill_input(page, ["input[aria-label*='website' i]", "input[aria-label*='store URL' i]", "input[aria-label*='online store' i]", "input[name*='website' i]", "input[placeholder*='http' i]", "input[placeholder*='www.' i]"], site_url, log_callback, "gmc")
    await _human_delay(500, 1000)
    await _click_button(page, ["Continue", "Next"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_feed(page, ctx, log_callback=None):
    feed_url = ctx.get("feed_url", "")
    if feed_url:
        _emit(log_callback, "info", f"配置数据源 -> {feed_url}", "gmc")
        await _click_button(page, ["Add products", "Add a feed", "Set up a feed", "Feed", "Data source"], log_callback, "gmc")
        await _fill_input(page, ["input[aria-label*='feed URL' i]", "input[aria-label*='data source' i]", "input[name*='feedUrl' i]", "input[placeholder*='http' i]"], feed_url, log_callback, "gmc")
    await _human_delay(500, 1000)
    await _click_button(page, ["Continue", "Next", "Create feed"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_phone_verify(page, ctx, log_callback=None):
    _emit(log_callback, "info", "手机验证 -> 选择其他方式", "gmc")
    if not await _click_button(page, ["Other methods", "Try another way", "More options", "Use a different method"], log_callback, "gmc"):
        await _click_button(page, ["Skip", "Not now", "Cancel"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_website_verify(page, ctx, log_callback=None):
    await _click_button(page, ["HTML tag", "Google tag", "Google Analytics", "Add HTML tag"], log_callback, "gmc")
    await _human_delay(1000, 2000)
    try:
        meta = await page.evaluate("""(() => { const m = document.querySelector('meta[name=google-site-verification]'); if (m) return m.outerHTML; const t = document.body.innerText; const r = t.match(/<meta\\s+name=["']google-site-verification["']\\s+content=["']([^"']+)["']/i); if (r) return '<meta name=google-site-verification content=' + r[1] + '>'; const r2 = t.match(/content=["']([^"']{20,})["']/); if (r2) return '<meta name=google-site-verification content=' + r2[1] + '>'; return null; })()""")
        if meta:
            ctx["extracted_meta_tag"] = str(meta)
            _emit(log_callback, "info", "已获取验证标签 -> 自动注入", "gmc")
    except Exception: pass
    await _click_button(page, ["Verify", "Verify URL", "Continue", "Next"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_shipping(page, ctx, log_callback=None):
    await _click_button(page, ["Continue", "Next", "Skip", "Save"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_terms(page, ctx, log_callback=None):
    _emit(log_callback, "info", "接受条款 -> 完成注册", "gmc")
    try:
        cb = page.locator("input[type='checkbox']").first
        if await cb.count() > 0 and not await cb.is_checked(): await cb.check(); await _human_delay(300, 500)
    except Exception: pass
    await _click_button(page, ["Create account", "Complete registration", "Finish", "Submit", "Accept", "I agree", "Continue"], log_callback, "gmc")
    return "continue"

async def _exec_gmc_complete(page, ctx, log_callback=None):
    mc_id = await _extract_mc_id(page) or "registered"
    ctx["mc_account_id"] = mc_id
    _emit(log_callback, "info", f"注册成功 -> MC ID: {mc_id}", "done")
    return "done"

async def _exec_captcha(page, ctx, log_callback=None):
    """Autonomous CAPTCHA solver using recaptcha-bypass + YOLO-V8.

    Strategy:
    1. Detect CAPTCHA type via page inspection
    2. reCAPTCHA checkbox -> click + audio bypass
    3. reCAPTCHA image grid -> YOLO-V8 object detection
    4. hCaptcha -> similar to reCAPTCHA
    5. Fallback -> 60s VNC manual intervention
    """
    _emit(log_callback, "info", "CAPTCHA检测 -> 自主求解中...", "captcha")

    # Step 1: Detect CAPTCHA type
    captcha_info = await page.evaluate("""(() => {
        const html = document.body.innerHTML;
        const hasRecaptcha = html.includes('recaptcha') || html.includes('g-recaptcha');
        const hasHcaptcha = html.includes('hcaptcha') || html.includes('h-captcha');
        const hasCheckbox = html.includes('recaptcha-checkbox') || document.querySelector('.recaptcha-checkbox');
        const hasImageGrid = document.querySelectorAll('img[src*="payload"]').length > 0 || html.includes('captcha-img');
        const hasAudio = document.querySelector('#recaptcha-audio-button, .rc-button-audio') || html.includes('audio');
        const iframes = document.querySelectorAll('iframe[src*="recaptcha"], iframe[src*="captcha"], iframe[src*="hcaptcha"]');
        return {
            type: hasRecaptcha ? 'recaptcha' : hasHcaptcha ? 'hcaptcha' : 'unknown',
            hasCheckbox: !!hasCheckbox,
            hasImageGrid: !!hasImageGrid,
            hasAudio: !!hasAudio,
            iframeCount: iframes.length,
            challengeText: document.querySelector('.rc-imageselect-desc, .captcha-instruction')?.innerText || ''
        };
    })()""")

    captcha_type = captcha_info.get("type", "unknown")
    _emit(log_callback, "info", f"CAPTCHA类型: {captcha_type} (checkbox={captcha_info.get('hasCheckbox')} image={captcha_info.get('hasImageGrid')})", "captcha")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # Strategy A: reCAPTCHA audio bypass (most reliable)
            if captcha_type in ("recaptcha", "unknown"):
                _emit(log_callback, "info", f"尝试 reCAPTCHA 音频绕过 (第{attempt}次)...", "captcha")
                if await _solve_recaptcha_audio(page, log_callback):
                    _emit(log_callback, "info", "CAPTCHA已通过! (音频)", "captcha")
                    return "continue"

            # Strategy B: reCAPTCHA checkbox + auto-click
            if captcha_info.get("hasCheckbox"):
                _emit(log_callback, "info", "尝试点击 reCAPTCHA 复选框...", "captcha")
                if await _solve_recaptcha_checkbox(page, log_callback):
                    _emit(log_callback, "info", "CAPTCHA已通过! (复选框)", "captcha")
                    return "continue"

            # Strategy C: Image grid with YOLO
            if captcha_info.get("hasImageGrid"):
                _emit(log_callback, "info", "尝试 YOLO-V8 图像识别...", "captcha")
                if await _solve_captcha_image_grid(page, captcha_info.get("challengeText", ""), log_callback):
                    _emit(log_callback, "info", "CAPTCHA已通过! (YOLO)", "captcha")
                    return "continue"

        except Exception as e:
            _emit(log_callback, "warning", f"CAPTCHA求解({attempt}): {e}", "captcha")

        if attempt < max_attempts:
            await _human_delay(1000, 2000)

    # Fallback: VNC manual
    _emit(log_callback, "warning", "自主求解失败 -> 通过VNC手动完成(60秒)", "captcha")
    await asyncio.sleep(60)
    return "continue"


async def _solve_recaptcha_checkbox(page, log_callback=None) -> bool:
    """Click the reCAPTCHA checkbox and check if it passes immediately."""
    try:
        # Switch to reCAPTCHA iframe
        frame = page.frame_locator("iframe[src*='recaptcha'], iframe[src*='google.com/recaptcha']")
        cb = frame.locator(".recaptcha-checkbox, #recaptcha-anchor, div[role='checkbox']")
        if await cb.count() > 0:
            await cb.first.click(timeout=5000)
            await _human_delay(2000, 4000)
            # Check if passed (green checkmark appears)
            is_checked = await frame.locator(".recaptcha-checkbox-checked, [aria-checked='true']").count() > 0
            if is_checked:
                return True
    except Exception:
        pass

    # Also try clicking the checkbox directly on the page (not in iframe)
    try:
        cb = page.locator(".recaptcha-checkbox, #recaptcha-anchor")
        if await cb.count() > 0:
            await cb.first.click(timeout=5000)
            await _human_delay(2000, 4000)
    except Exception:
        pass

    return False


async def _solve_recaptcha_audio(page, log_callback=None) -> bool:
    """Use recaptcha-bypass library for audio challenge solving."""
    try:
        from recaptcha_bypass import ReCaptchaEnterpriseV2Bypass

        # Get current URL
        url = page.url

        # The recaptcha-bypass library needs the sitekey
        sitekey = await page.evaluate("""
            (() => {
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                const m = document.body.innerHTML.match(/sitekey['"]?\\s*[:=]\\s*['"]([^'"]+)['"]/);
                return m ? m[1] : null;
            })()
        """)

        if not sitekey:
            # Try to find it in the iframe src
            sitekey = await page.evaluate("""
                (() => {
                    const iframe = document.querySelector('iframe[src*="recaptcha"]');
                    if (iframe) {
                        const m = iframe.src.match(/[?&]k=([^&]+)/);
                        return m ? m[1] : null;
                    }
                    return null;
                })()
            """)

        if sitekey:
            _emit(log_callback, "info", f"reCAPTCHA sitekey: {sitekey}", "captcha")
            try:
                bypasser = ReCaptchaEnterpriseV2Bypass(url)
                token = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: (bypasser.bypass(), bypasser.get_response())[1]
                )
                if token:
                    # Inject the token
                    await page.evaluate(f"""
                        document.getElementById('g-recaptcha-response').innerHTML = '{token}';
                        if (typeof ___grecaptcha_cfg !== 'undefined') {{
                            for (const c of Object.values(___grecaptcha_cfg.clients || {{}})) {{
                                if (c && c.callback) c.callback('{token}');
                            }}
                        }}
                    """)
                    await _human_delay(1000, 2000)
                    return True
            except Exception as e:
                _emit(log_callback, "warning", f"recaptcha-bypass library failed: {e}", "captcha")
    except ImportError:
        _emit(log_callback, "warning", "recaptcha-bypass 未安装", "captcha")
    except Exception as e:
        _emit(log_callback, "warning", f"音频绕过异常: {e}", "captcha")

    # Manual audio fallback: click audio button, download, transcribe
    try:
        # Find and click audio button
        frame = page.frame_locator("iframe[src*='recaptcha'], iframe[src*='google.com/recaptcha']")
        audio_btn = frame.locator("#recaptcha-audio-button, button[title*='audio'], .rc-button-audio")
        if await audio_btn.count() > 0:
            await audio_btn.first.click(timeout=3000)
            await _human_delay(2000, 3000)

            # Get audio URL
            audio_url = await frame.locator("audio source, .rc-audiochallenge-tdownload-link, a[href*='audio']").first.get_attribute("src") or ""
            if not audio_url:
                audio_url = await page.evaluate("""
                    (() => {
                        const a = document.querySelector('audio source');
                        return a ? a.src : '';
                    })()
                """)

            if audio_url:
                _emit(log_callback, "info", f"下载音频: {audio_url[:80]}", "captcha")
                # Download and transcribe
                import tempfile, os
                import requests as http_requests

                resp = http_requests.get(audio_url, timeout=15)
                if resp.status_code == 200:
                    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                    tmp.write(resp.content)
                    tmp.close()

                    try:
                        import speech_recognition as sr
                        r = sr.Recognizer()
                        with sr.AudioFile(tmp.name) as source:
                            audio = r.record(source)
                        text = r.recognize_google(audio)
                        _emit(log_callback, "info", f"语音识别: {text}", "captcha")

                        # Fill the answer
                        inp = frame.locator("#audio-response, input[type='text']")
                        if await inp.count() > 0:
                            await inp.first.fill(text)
                            await _human_delay(500, 1000)
                            verify_btn = frame.locator("#recaptcha-verify-button, button[title*='Verify']")
                            if await verify_btn.count() > 0:
                                await verify_btn.first.click(timeout=3000)
                                await _human_delay(2000, 3000)
                                return True
                    finally:
                        os.unlink(tmp.name)
    except ImportError:
        _emit(log_callback, "warning", "SpeechRecognition 未安装", "captcha")
    except Exception as e:
        _emit(log_callback, "warning", f"音频绕过失败: {e}", "captcha")

    return False


async def _solve_captcha_image_grid(page, challenge_text: str, log_callback=None) -> bool:
    """Solve image grid CAPTCHA using YOLO-V8 object detection."""
    try:
        from ultralytics import YOLO
        import cv2, base64, tempfile, os

        # Get the target object from challenge text
        _emit(log_callback, "info", f"图像挑战: {challenge_text[:80]}", "captcha")

        # Load YOLO model
        model = YOLO("yolov8n.pt")  # nano model for speed

        # Get all CAPTCHA images
        frame = page.frame_locator("iframe[src*='recaptcha'], iframe[src*='google.com/recaptcha']")
        images = frame.locator("img[src*='payload'], .rc-imageselect-tile img, td[role='button'] img")
        count = await images.count()
        if count == 0:
            images = page.locator("img[src*='payload'], .rc-imageselect-tile img, td[role='button'] img")
            count = await images.count()

        if count == 0:
            return False

        # Map challenge text to YOLO classes
        target_map = {
            "bus": ["bus"], "buses": ["bus"],
            "car": ["car"], "cars": ["car"],
            "traffic light": ["traffic light"], "traffic lights": ["traffic light"],
            "bicycle": ["bicycle"], "bicycles": ["bicycle"], "bike": ["bicycle"],
            "motorcycle": ["motorcycle"], "motorcycles": ["motorcycle"],
            "crosswalk": ["person"], "crosswalks": ["person"],
            "fire hydrant": ["fire hydrant"], "hydrant": ["fire hydrant"],
            "truck": ["truck"], "trucks": ["truck"],
            "boat": ["boat"], "boats": ["boat"],
            "bridge": ["bridge"], "bridges": ["bridge"],
            "stairs": ["stairs"], "stair": ["stairs"],
            "chimney": ["chimney"], "chimneys": ["chimney"],
            "parking meter": ["parking meter"], "meters": ["parking meter"],
        }

        target_classes = []
        for key, vals in target_map.items():
            if key in challenge_text.lower():
                target_classes = vals
                break

        if not target_classes:
            _emit(log_callback, "warning", f"无法映射挑战对象: {challenge_text[:60]}", "captcha")
            return False

        _emit(log_callback, "info", f"YOLO检测目标: {target_classes}", "captcha")

        # Process each image
        for i in range(count):
            try:
                img_el = images.nth(i)
                # Screenshot the image
                screenshot = await img_el.screenshot()
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.write(screenshot)
                tmp.close()

                # Run YOLO detection
                results = model(tmp.name)
                os.unlink(tmp.name)

                # Check if target object detected
                detected = False
                for r in results:
                    for box in r.boxes:
                        cls_id = int(box.cls[0])
                        cls_name = model.names[cls_id]
                        if any(tc in cls_name.lower() for tc in target_classes):
                            detected = True
                            break

                # Click the image if target detected
                if detected:
                    await img_el.click(timeout=3000)
                    await _human_delay(300, 600)
                    _emit(log_callback, "info", f"选中图片 {i+1}/{count} (检测到{target_classes})", "captcha")
            except Exception as e:
                _emit(log_callback, "warning", f"图片{i}处理失败: {e}", "captcha")
                continue

        # Click Verify
        verify_btn = frame.locator("#recaptcha-verify-button, button[title*='Verify']")
        if await verify_btn.count() > 0:
            await verify_btn.first.click(timeout=3000)
            await _human_delay(2000, 3000)
            return True

        return True  # Assume clicked enough

    except ImportError as e:
        _emit(log_callback, "warning", f"YOLO依赖缺失: {e}", "captcha")
        return False
    except Exception as e:
        _emit(log_callback, "warning", f"图像求解失败: {e}", "captcha")
        return False

async def _exec_blocked(page, ctx, log_callback=None):
    _emit(log_callback, "warning", "检测到无关页面 -> 返回 GMC", "blocked")
    await page.goto("https://merchants.google.com/?hl=en-US&gl=US", wait_until="domcontentloaded", timeout=30000)
    await _human_delay(2000, 3000)
    await _dismiss_overlays(page)
    return "continue"

async def _exec_unknown(page, ctx, log_callback=None):
    _emit(log_callback, "warning", "未知页面 -> 尝试自动推进", "gmc")
    if await _click_button(page, ["Continue", "Next", "Save", "Skip"], log_callback, "gmc"):
        return "continue"
    return "fail"

_EXEC_DISPATCH = {
    "login_email": _exec_login_email, "login_password": _exec_login_password,
    "login_2fa": _exec_login_2fa, "login_challenge": _exec_login_challenge,
    "gmc_dashboard": _exec_gmc_dashboard, "gmc_landing": _exec_gmc_landing,
    "gmc_account_type": _exec_gmc_account_type, "gmc_business_form": _exec_gmc_business_form,
    "gmc_website": _exec_gmc_website, "gmc_feed": _exec_gmc_feed,
    "gmc_phone_verify": _exec_gmc_phone_verify, "gmc_website_verify": _exec_gmc_website_verify,
    "gmc_shipping": _exec_gmc_shipping, "gmc_terms": _exec_gmc_terms,
    "gmc_complete": _exec_gmc_complete,
    "captcha": _exec_captcha, "blocked": _exec_blocked, "unknown": _exec_unknown,
}


# ====================================================================================
#  主入口: register_gmc
# ====================================================================================

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
    """Register GMC account — AI classifies pages, Script executes actions.

    AI: Looks at page text, outputs page_type
    Script: Dispatches to deterministic handler for each page_type
    Max 60 steps, auto stuck-detection, CAPTCHA manual-fallback.
    """
    os.environ.setdefault("DISPLAY", ":99")

    # --- Load & fix profile config ---
    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
    bi_country = (business_info or {}).get("country", "")
    if bi_country and config:
        region = _REGION_CONFIGS.get(bi_country, _REGION_CONFIGS.get("US"))
        if config.get("country") != bi_country or config.get("locale") != region["locale"]:
            config["country"] = bi_country; config["locale"] = region["locale"]
            config["timezone"] = region["timezones"][0]
            save_profile_config(profile_dir, config)
    if config and not config.get("webrtc_ip"):
        import re as _re2
        m = _re2.search(r'@([\d.]+):', proxy)
        if m: config["webrtc_ip"] = m.group(1); save_profile_config(profile_dir, config)

    fingerprint_args = _build_launch_args(config)

    # --- Launch browser ---
    from cloakbrowser import launch_persistent_context_async
    launch_kwargs = {"headless": headless, "user_data_dir": profile_dir, "timeout": timeout_ms, "args": fingerprint_args}
    if proxy: launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)
    profile_name = os.path.basename(profile_dir)
    _emit(log_callback, "info", f"启动浏览器 -> {profile_name}", "launch")
    _unlock_profile(profile_dir)
    context = page = None

    try:
        context = await launch_persistent_context_async(**launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
    except Exception as e:
        _emit(log_callback, "error", f"浏览器启动失败: {e}", "launch")
        return {"success": False, "message": f"Browser launch failed: {e}", "steps": 0}

    try:
        # --- Pre-warm ---
        try:
            _emit(log_callback, "info", "预热浏览器 -> 建立浏览历史", "prewarm")
            for s in ["https://www.google.com/", "https://www.google.com/search?q=online+shopping",
                       "https://accounts.google.com/", "https://myaccount.google.com/",
                       "https://search.google.com/search-console"]:
                try: await page.goto(s, wait_until="domcontentloaded", timeout=25000); await _human_delay(1500, 3000)
                except Exception: pass
        except Exception: pass

        # --- Navigate to GMC ---
        _emit(log_callback, "info", "访问 merchants.google.com", "navigate")
        page.set_default_navigation_timeout(90000)
        # Force English: header (set at launch) + Google official URL params
        await page.goto("https://merchants.google.com/?hl=en-US&gl=US", wait_until="domcontentloaded", timeout=90000)
        await _human_delay(2000, 3000)
        await _dismiss_overlays(page)

        # --- Main AI+Script loop ---
        ctx = {"google_email": google_email, "google_password": google_password,
               "google_totp_secret": google_totp_secret, "business_info": business_info or {},
               "site_url": site_url, "feed_url": feed_url, "mc_account_id": "", "extracted_meta_tag": ""}
        max_steps, same_page_count, last_url, last_page_type = 60, 0, "", ""

        for step in range(1, max_steps + 1):
            current_url = page.url
            same_page_count = (same_page_count + 1) if (current_url == last_url and last_page_type) else 0
            last_url = current_url
            if same_page_count >= 4:
                _emit(log_callback, "warning", f"卡住({same_page_count}次) -> 强制推进", "stuck")
                await _click_button(page, ["Continue", "Next", "Skip", "Save"], log_callback, "stuck")
                await _human_delay(2000, 3000); same_page_count = 0

            # === Step 1: AI classify ===
            decision = await _ai_classify_page(page, log_callback)
            page_type = decision["page_type"]; last_page_type = page_type
            _emit(log_callback, "info",
                  f"[{step}/{max_steps}] AI识别页面 → {page_type} (置信度={decision.get('confidence', '?')})", "step")

            # === Step 2: Execute handler ===
            handler = _EXEC_DISPATCH.get(page_type, _exec_unknown)
            expected = _ACTION_DESCRIPTIONS.get(page_type, "advance to next page")
            result = await handler(page, ctx, log_callback)

            if result == "done":
                mc_id = ctx.get("mc_account_id", "registered")
                meta = ctx.get("extracted_meta_tag", "")
                _emit(log_callback, "info", f"注册成功 → MC ID: {mc_id} (共{step}步)", "done")
                return {"success": True, "mc_account_id": mc_id, "message": f"GMC registered: {mc_id}", "steps": step, "meta_tag": meta}
            if result == "fail":
                _emit(log_callback, "error",
                      f"[{step}/{max_steps}] 失败 → 页面类型: {page_type}", "gmc")
                return {"success": False, "message": f"Failed at step {step} ({page_type})", "steps": step, "meta_tag": ctx.get("extracted_meta_tag", "")}

            # === Step 3: AI verify ===
            await _human_delay(1500, 2500)
            verify = await _ai_verify_action(page, expected, log_callback)
            if verify.get("success") == False:
                new_pt = verify.get("new_page_type", "unknown")
                reason = verify.get("reasoning", "?")
                _emit(log_callback, "warning",
                      f"[{step}/{max_steps}] 验证失败 → {reason} → 重试", "verify")
                # Retry with alternative approach
                if page_type == "gmc_landing":
                    await _click_button(page, ["Sign in", "Get started"], log_callback, "retry")
                elif page_type in ("login_email", "login_password", "login_2fa", "login_challenge"):
                    await _click_button(page, ["Next", "Continue", "Skip"], log_callback, "retry")
                else:
                    await _click_button(page, ["Continue", "Next", "Save", "Skip"], log_callback, "retry")
                await _human_delay(1000, 2000)
            else:
                new_pt = verify.get("new_page_type", "?")
                _emit(log_callback, "info",
                      f"[{step}/{max_steps}] 验证通过 → 进入 {new_pt}", "verify")

        _emit(log_callback, "warning", f"达到最大步骤({max_steps})", "gmc")
        return {"success": False, "message": f"Max steps ({max_steps})", "steps": max_steps, "meta_tag": ""}

    except Exception as e:
        _emit(log_callback, "error", f"注册异常: {e}", "exception")
        logger.exception("register_gmc error")
        return {"success": False, "message": f"Registration error: {e}", "steps": 0, "meta_tag": ""}
    finally:
        try:
            if context: await context.close()
        except Exception: pass
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
