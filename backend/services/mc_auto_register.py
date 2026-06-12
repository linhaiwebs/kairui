"""
Google Merchant Center 自动化注册 + CloakBrowser 指纹环境管理

每个 profile 目录是一个完整的独立浏览器环境：
- cookies/localStorage 自动持久化
- 指纹参数（OS、GPU、屏幕、硬件等）由 config.json 控制
- 代理 IP 由 config.json 配置

Profile 目录结构：
    backend/profiles/<name>/
        config.json          ← 指纹 + 代理 + Google 账号信息
        Default/             ← CloakBrowser 自动管理的 cookies/storage

Usage::

    from services.mc_auto_register import create_profile, register_mc_account

    # 创建新 profile（随机指纹）
    cfg = create_profile("store-001", google_email="store@gmail.com",
                         proxy="socks5://user:pass@1.2.3.4:1080")

    # 注册 MC
    result = asyncio.run(register_mc_account(
        profile_dir=cfg["dir"],
        site_domain="example.com",
        feed_url="https://example.com/wp-content/uploads/google-feed.xml",
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
    config["proxy"] = proxy.replace("socks5h://", "socks5://") if proxy else ""
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


class StepFailed(Exception):
    """Raised when a GMC step fails in strict mode — stops the flow immediately."""
    pass


def _make_step_helpers(page, _emit, timeout_ms, strict=False):
    """Create reusable helper functions for GMC Next step automation.

    When strict=True, any selector failure raises StepFailed instead of
    returning False, so the flow stops at the first problem for diagnosis.
    """

    class Helpers:
        current_step = ""  # set via _enter_step → helps.set_step()

        @staticmethod
        def set_step(name: str):
            Helpers.current_step = name

        @staticmethod
        async def _fail(reason: str):
            """Handle failure: warn in normal mode, raise in strict mode."""
            step = Helpers.current_step
            if strict:
                ts = _dt.now().strftime("%Y%m%d_%H%M%S")
                path = f"/tmp/gmc_next_{step}_{ts}.png"
                try:
                    await page.screenshot(path=path)
                    _emit("error", f"❌ {step}: {reason}\n   截图: {path}\n   page: {page.url[:120]}", step)
                except Exception as e:
                    _emit("error", f"❌ {step}: {reason} (截图失败: {e})", step)
                raise StepFailed(f"[{step}] {reason}")
            _emit("warning", f"{step}: {reason}", step)

        @staticmethod
        async def click_any(selectors: list) -> bool:
            """Try each selector, click first visible match. Return True if clicked."""
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click(timeout=5000)
                        await asyncio.sleep(1)
                        return True
                except Exception:
                    continue
            if strict:
                await Helpers._fail(f"click_any 失败 — {len(selectors)} 个选择器均未匹配")
            return False

        @staticmethod
        async def fill_any(selectors: list, value: str) -> bool:
            """Try each selector, fill first visible match. Return True if filled."""
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.fill(value, timeout=5000)
                        return True
                except Exception:
                    continue
            if strict:
                await Helpers._fail(f"fill_any 失败 — {len(selectors)} 个选择器，目标值: {value[:60]}")
            return False

        @staticmethod
        async def fill_select(selectors: list, value: str) -> bool:
            """Fill a <select> or mwc-select dropdown."""
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "select":
                            await el.select_option(value, timeout=5000)
                            return True
                        await el.click(timeout=5000)
                        await asyncio.sleep(1)
                        for opt in [
                            f"mwc-list-item:has-text('{value}')",
                            f"li:has-text('{value}')",
                            f"[role='option']:has-text('{value}')",
                            f"span:has-text('{value}')",
                        ]:
                            try:
                                opt_el = page.locator(opt).first
                                if await opt_el.count() > 0 and await opt_el.is_visible():
                                    await opt_el.click(timeout=3000)
                                    await asyncio.sleep(0.5)
                                    return True
                            except Exception:
                                continue
                        await page.keyboard.press("Escape")
                        return False
                except Exception:
                    continue
            if strict:
                await Helpers._fail(f"fill_select 失败 — {len(selectors)} 个选择器，目标值: {value}")
            return False

        @staticmethod
        async def click_continue() -> bool:
            """Click Continue/Next/Save button."""
            for sel in [
                "mwc-button:has-text('Continue')", "mwc-button:has-text('Next')",
                "mwc-button:has-text('Save')", "mwc-button:has-text('Submit')",
                "button:has-text('Continue')", "button:has-text('Next')",
                "button:has-text('Save')", "button:has-text('Submit')",
                "span.mdc-button__label:text-is('Continue')",
                "input[type='submit']",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.click(timeout=5000)
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue
            if strict:
                await Helpers._fail("click_continue 失败 — 找不到 Continue/Next/Save/Submit 按钮")
            return False

        @staticmethod
        async def debug_screenshot(reason: str):
            """Save screenshot for debugging a failed step."""
            step = Helpers.current_step
            if strict:
                await Helpers._fail(reason)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            path = f"/tmp/gmc_next_{step}_{ts}.png"
            try:
                await page.screenshot(path=path)
                _emit("warning", f"{step}: {reason} — 截图: {path}", step)
            except Exception as e:
                _emit("warning", f"{step}: {reason} (截图失败: {e})", step)

    return Helpers()


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


async def _dump_step_dom(page, recon_dir: str, step_name: str):
    """Save screenshot + DOM structure JSON for a step."""
    import json as _json
    os.makedirs(recon_dir, exist_ok=True)
    safe = step_name.replace(" ", "_").replace("/", "_")
    ss_path = os.path.join(recon_dir, f"{safe}.png")
    dom_path = os.path.join(recon_dir, f"{safe}.json")
    try:
        await page.screenshot(path=ss_path, full_page=False)
    except Exception:
        pass
    try:
        dom = await page.evaluate(_DUMP_DOM_JS)
        with open(dom_path, "w", encoding="utf-8") as f:
            _json.dump(dom, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return ss_path, dom_path


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

    success = "accounts.google.com" not in page.url
    if success:
        _emit("info", "Google 登录完成", "google_login")
    else:
        _emit("warning", f"可能仍在登录页面: {page.url[:80]}", "google_login")
    return success


async def register_mc_account(
    profile_dir: str,
    site_domain: str,
    google_email: str = "",
    google_password: str = "",
    google_totp_secret: str = "",
    site_title: str = "",
    feed_url: str = "",
    country: str = "US",
    timezone: str = "",
    headless: bool = True,
    timeout_ms: int = 180000,  # slow proxies need generous timeout
    log_callback=None,
    business_info: dict | None = None,
    return_policy_url: str = "",
    wp_url: str = "",
    wp_username: str = "",
    wp_password: str = "",
    recon_dir: str = "",
) -> dict:
    """注册 Google Merchant Center Next 账号。

    从 profile_dir/config.json 读取指纹 + 代理配置，
    启动 CloakBrowser 反检测浏览器，自动完成 GMC Next 注册全流程（12 步向导）。
    log_callback(level, message, step) — 可选，用于实时日志推送到前端。
    recon_dir — 设置后每步自动导出截图+DOM JSON 用于诊断。
    """
    def _emit(level: str, msg: str, step: str = ""):
        log_func = {"info": logger.info, "warning": logger.warning, "error": logger.error}.get(level, logger.info)
        log_func("%s", msg)
        if log_callback:
            try:
                log_callback(level, msg, step)
            except Exception:
                pass

    async def _enter_step(step_name: str, msg: str):
        result["step"] = step_name
        _emit("info", msg, step_name)
        helpers.set_step(step_name)
        if recon_dir:
            await _dump_step_dom(page, recon_dir, step_name)

    _emit("info", "=" * 50)
    _emit("info", f"MC 注册开始 — site={site_domain} profile={os.path.basename(profile_dir)}")
    _emit("info", f"参数: country={country} headless={headless} timeout={timeout_ms/1000}s feed={'✓' if feed_url else '✗'}")

    try:
        from cloakbrowser import launch_persistent_context_async
    except ImportError:
        _emit("error", "cloakbrowser 未安装", "import")
        return {
            "success": False,
            "message": "cloakbrowser 未安装，请运行: pip install cloakbrowser",
            "step": "import",
        }

    if not os.path.isdir(profile_dir):
        _emit("error", f"Profile 目录不存在: {profile_dir}", "profile_dir")
        return {"success": False, "message": f"Profile 目录不存在: {profile_dir}", "step": "profile_dir"}

    # 读取 profile 配置
    _emit("info", "正在加载 profile 配置...", "config")
    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
    tz = timezone or config.get("timezone", "America/Chicago")
    locale = config.get("locale", "en-US")
    fingerprint_args = _build_launch_args(config)

    title = site_title or site_domain.replace("www.", "").split(".")[0].capitalize()
    website_url = f"https://{site_domain}"
    _emit("info", f"配置加载完成 — platform={config.get('platform','?')} proxy={'✓' if proxy else '✗'} title={title} url={website_url}", "config")

    browser_ctx = None
    result = {"success": False, "mc_account_id": "", "step": ""}
    mc_id = ""

    try:
        launch_kwargs = {
            "headless": headless,
            "timezone": tz,
            "locale": locale,
            "args": fingerprint_args,
            "humanize": True,
            "stealth_args": False,
        }
        if proxy:
            launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)
            launch_kwargs["geoip"] = True

        _emit("info", f"正在启动浏览器... (headless={headless})", "launch")
        _unlock_profile(profile_dir)
        browser_ctx = await launch_persistent_context_async(profile_dir, **launch_kwargs)
        if browser_ctx.pages:
            page = browser_ctx.pages[0]
        else:
            page = await browser_ctx.new_page()
        page.set_default_timeout(timeout_ms)
        helpers = _make_step_helpers(page, _emit, timeout_ms, strict=bool(recon_dir))
        _emit("info", f"浏览器启动成功，页面就绪 (超时={timeout_ms/1000}s)", "launch")

        # --- Step 1: Navigate ---
        await _enter_step("navigate", "步骤 1: 访问 GMC 首页 — merchants.google.com ...")
        try:
            await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=180000)
            _emit("info", f"GMC 首页已加载 — URL: {page.url[:80]}", "navigate")
        except Exception as e:
            _emit("error", f"访问 GMC 首页超时/失败: {e}", "navigate")
            result["message"] = f"访问 Google Merchant Center 失败(网络超时): {e}"
            return result
        await asyncio.sleep(3)

        if "accounts.google.com" in page.url:
            _emit("warning", f"Google 未登录 — 当前 URL: {page.url[:80]}", "navigate")
            if google_email and google_password:
                _emit("info", f"检测到 Google 账户凭据，尝试自动登录...", "google_login")
                login_ok = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=timeout_ms,
                    recon_dir=recon_dir,
                )
                if login_ok:
                    _emit("info", "Google 自动登录成功，继续 MC 注册流程", "google_login")
                    await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=180000)
                    await asyncio.sleep(3)
                else:
                    _emit("error", "Google 自动登录失败", "google_login")
                    return {
                        "success": False,
                        "message": "Google 自动登录失败，请检查账户凭据或 2FA 密钥",
                        "step": "google_login",
                    }
            else:
                return {
                    "success": False,
                    "message": f"Google 未登录。请先在 headless=False 下打开 profile '{os.path.basename(profile_dir)}' 并手动登录，或在品牌套件中关联 Google 账户",
                    "step": "google_login",
                }
        _emit("info", "Google 已登录", "navigate")

        # If brand kit specifies a Google account, ensure correct account is logged in
        if google_email:
            logged_in = await _get_logged_in_email(page)
            if logged_in and google_email.lower() != logged_in.lower():
                _emit("warning", f"Profile 当前登录 {logged_in}，但品牌套件指定 {google_email}，正在切换账户...", "google_login")
                await page.goto("https://accounts.google.com/Logout", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                login_ok = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=timeout_ms,
                    recon_dir=recon_dir,
                )
                if login_ok:
                    _emit("info", f"已切换到品牌套件账户: {google_email}", "google_login")
                else:
                    _emit("error", f"切换账户失败: {google_email}", "google_login")
                    return {
                        "success": False,
                        "message": f"切换到 Google 账户 {google_email} 失败，请检查凭据",
                        "step": "google_login",
                    }

        # ================================================================
        # Detect current GMC state — are we in wizard, dashboard, or setup?
        # ================================================================
        gmc_state = await _detect_gmc_page(page)
        _emit("info", f"GMC 状态检测: phase={gmc_state['phase']} wizard_step={gmc_state.get('wizard_step',0)} url={gmc_state.get('url','')[:80]}", "state_detect")

        if gmc_state["phase"] == "dashboard":
            _emit("info", "GMC 账户已注册，跳过注册向导，直接进入设置流程", "state_detect")
            # Extract MC ID from page content for setup URL
            if not mc_id:
                m = _re.search(r'/a/(\d+)', page.url)
                if not m:
                    m = _re.search(r'/mc/(\d+)', page.url)
                if m:
                    mc_id = m.group(1)
                    result["mc_account_id"] = mc_id
                    _emit("info", f"从 URL 提取 MC ID: {mc_id}", "state_detect")
            result["wizard_skipped"] = True
            # Skip wizard entirely - fall through to setup section below

        elif gmc_state["phase"] == "setup":
            _emit("info", "已在设置流程中，跳过注册向导", "state_detect")
            result["wizard_skipped"] = True
            # Fall through to setup section

        elif gmc_state["phase"] == "wizard":
            _emit("info", f"进入 GMC 注册向导 (当前步骤: {gmc_state.get('wizard_step', '?')})", "state_detect")
            current_wizard_step = max(gmc_state.get("wizard_step", 1), 1)
            await _dismiss_overlays(page)

            # Fast-forward to current step: keep clicking Continue until we catch up
            if current_wizard_step > 1:
                _emit("info", f"检测到已跳过前 {current_wizard_step - 1} 步，快速推进中...", "fast_forward")
                for ff in range(current_wizard_step - 1):
                    clicked = False
                    for sel in [
                        "mwc-button:has-text('Continue')", "mwc-button:has-text('Next')",
                        "button:has-text('Continue')", "button:has-text('Next')",
                        "button[type='submit']",
                    ]:
                        try:
                            el = page.locator(sel).first
                            if await el.count() > 0 and await el.is_visible():
                                await el.click(timeout=3000)
                                clicked = True
                                await asyncio.sleep(3)
                                break
                        except Exception:
                            continue
                    if not clicked:
                        _emit("warning", f"快速推进第 {ff + 1} 步失败，可能已到末尾", "fast_forward")
                        break
                _emit("info", f"快速推进完成，当前位置: {page.url[:100]}", "fast_forward")

            # Re-detect after fast-forward
            gmc_state = await _detect_gmc_page(page)
            _emit("info", f"推进后状态: phase={gmc_state['phase']} wizard_step={gmc_state.get('wizard_step',0)}", "fast_forward")
            if gmc_state["phase"] != "wizard":
                _emit("info", "已离开注册向导，跳过剩余步骤", "fast_forward")
                result["wizard_skipped"] = True

        async def _run_wizard():
            # --- Step 2: "Do you sell products online?" → Yes ---
            await _enter_step("ask_sell", "步骤 2: 回答 '是否在线销售产品?' → Yes")
            if not await helpers.click_any([
                "mwc-radio[value='yes']", "div[role='radio']:has-text('Yes')",
                "label:has-text('Yes') input[type='radio']", "span:has-text('Yes')",
                "mwc-button:has-text('Yes')", "button:has-text('Yes')",
            ]):
                _emit("info", "未找到 'Yes' 选项，可能已跳过此步骤", "ask_sell")
            await helpers.click_continue()

            # --- Step 3: Enter online store URL ---
            await _enter_step("enter_url", f"步骤 3: 输入商店 URL — https://{site_domain}")
            website_url = f"https://{site_domain}"
            if not await helpers.fill_any([
                "mwc-textfield[label*='website']", "mwc-textfield[label*='store']",
                "mwc-textfield[label*='URL']", "mwc-textfield[label*='shop']",
                "input[name='websiteUrl']", "input[name='website']",
                "input[aria-label*='website']", "input[aria-label*='store URL']",
                "input[type='url']", "input[placeholder*='https://']",
                "input[placeholder*='example.com']",
            ], website_url):
                await helpers.debug_screenshot("找不到商店 URL 输入框")
            await helpers.click_continue()

            # --- Step 4: "Do you have a physical store?" → No ---
            await _enter_step("ask_physical", "步骤 4: 回答 '是否有实体店?' → No")
            if not await helpers.click_any([
                "mwc-radio[value='no']", "div[role='radio']:has-text('No')",
                "div[role='radio']:has-text('No physical')",
                "label:has-text('No') input[type='radio']",
                "mwc-button:has-text('No')", "button:has-text('No')",
            ]):
                _emit("info", "未找到 'No' 选项，可能已跳过", "ask_physical")
            await helpers.click_continue()

            # --- Step 5: Business center info ---
            await _enter_step("business_info", "步骤 5: 填写商户信息...")

            company = (business_info or {}).get("company_name") or site_title or site_domain
            display_name = (business_info or {}).get("company_name") or site_title or site_domain
            reg_country = (business_info or {}).get("country") or country or "US"
            country_name = {"US": "United States", "CA": "Canada", "GB": "United Kingdom", "CN": "China"}.get(reg_country, reg_country)

            if not await helpers.fill_any([
                "mwc-textfield[label*='company']", "mwc-textfield[label*='business name']",
                "mwc-textfield[label*='legal']", "input[name='accountName']",
                "input[name='companyName']", "input[aria-label*='company name']",
                "input[aria-label*='business name']", "input[aria-label*='legal name']",
            ], company):
                await helpers.debug_screenshot("找不到公司名称输入框")

            if not await helpers.fill_any([
                "mwc-textfield[label*='display']", "mwc-textfield[label*='merchant display']",
                "input[name='businessDisplayName']", "input[name='displayName']",
                "input[aria-label*='display name']", "input[aria-label*='merchant display']",
            ], display_name):
                _emit("info", "未找到商家显示名输入框（可能与公司名共用）", "business_info")

            if not await helpers.fill_select([
                "mwc-select[label*='country']", "mwc-select[label*='region']",
                "select[name='country']", "div[role='combobox'][aria-label*='country']",
            ], country_name):
                _emit("info", "未找到国家选择器，跳过", "business_info")

            await helpers.click_continue()

            # --- Step 6: Business address ---
            await _enter_step("business_address", "步骤 6: 填写企业地址...")
            bi = business_info or {}

            addr = bi.get("address") or "123 Main St"
            city = bi.get("city") or "Los Angeles"
            state = bi.get("state_code") or "CA"
            zipcode = bi.get("postcode") or "90001"

            if not await helpers.fill_any([
                "mwc-textfield[label*='address']", "mwc-textfield[label*='street']",
                "input[name='address']", "input[name='streetAddress']",
                "input[aria-label*='address']", "input[aria-label*='street']",
            ], addr):
                await helpers.debug_screenshot("找不到地址输入框")

            if not await helpers.fill_any([
                "mwc-textfield[label*='city']", "input[name='city']",
                "input[aria-label*='city']",
            ], city):
                _emit("info", "未找到城市输入框", "business_address")

            if not await helpers.fill_select([
                "mwc-select[label*='state']", "mwc-select[label*='province']",
                "select[name='state']", "select[name='province']",
                "div[role='combobox'][aria-label*='state']",
            ], state):
                if not await helpers.fill_any([
                    "mwc-textfield[label*='state']", "input[name='state']",
                    "input[aria-label*='state']",
                ], state):
                    _emit("info", "未找到州/省输入框", "business_address")

            if not await helpers.fill_any([
                "mwc-textfield[label*='ZIP']", "mwc-textfield[label*='postal']",
                "input[name='zip']", "input[name='postalCode']", "input[name='postcode']",
                "input[aria-label*='ZIP']", "input[aria-label*='postal']",
            ], zipcode):
                _emit("info", "未找到邮编输入框", "business_address")

            await helpers.click_continue()

            # --- Step 7: Website verification ---
            await _enter_step("verify_website", "步骤 7: 网站验证 — 选择 HTML 标签方式...")

            # Select HTML tag method
            if not await helpers.click_any([
                "mwc-radio[value*='tag']", "mwc-radio[value*='meta']",
                "mwc-radio[value*='html']", "div[role='radio']:has-text('HTML tag')",
                "div[role='radio']:has-text('meta tag')",
                "label:has-text('HTML tag') input[type='radio']",
                "span:has-text('Add an HTML tag')",
            ]):
                _emit("info", "未找到 HTML 标签验证方式选项，可能已默认选中", "verify_website")

            # Extract verification code
            verification_code = ""
            for sel in [
                "code", "pre", "[data-code]", "[data-verification-code]",
                "span:has-text('content=')", "div:has-text('google-site-verification')",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        text = await el.inner_text()
                        m = _re.search(r'content=["\']([^"\']{20,})["\']', text)
                        if m:
                            verification_code = m.group(1)
                            break
                        m = _re.search(r'([a-zA-Z0-9_-]{30,})', text)
                        if m:
                            verification_code = m.group(1)
                            break
                except Exception:
                    pass

            if not verification_code:
                try:
                    html = await page.content()
                    m = _re.search(r'content=["\']([a-zA-Z0-9_-]{20,})["\']', html)
                    if m:
                        verification_code = m.group(1)
                except Exception:
                    pass

            if verification_code:
                _emit("info", f"验证码提取成功: {verification_code[:20]}...", "verify_website")

                # Inject into WordPress if WP credentials available
                if wp_url and wp_username and wp_password:
                    try:
                        from services.wordpress_client import WordPressAdminSession
                        wp = WordPressAdminSession(wp_url, wp_username, wp_password)
                        inject_result = wp.inject_google_verification(verification_code, "meta")
                        if inject_result.get("success"):
                            _emit("info", "验证标签已注入 WordPress", "verify_website")
                        else:
                            _emit("warning", f"验证标签注入失败: {inject_result.get('message')}", "verify_website")
                    except Exception as e:
                        _emit("warning", f"WordPress 注入异常: {e}", "verify_website")

                # Wait for Google to detect
                await asyncio.sleep(5)
            else:
                _emit("warning", "未能提取验证码，继续流程...", "verify_website")

            # Click verify button
            if not await helpers.click_any([
                "mwc-button:has-text('Verify')", "mwc-button:has-text('Verify URL')",
                "mwc-button:has-text('Verify website')", "button:has-text('Verify')",
                "button:has-text('Verify website')", "button:has-text('I have added')",
                "button:has-text('Check')",
            ]):
                _emit("info", "未找到验证按钮，标签可能已自动检测", "verify_website")

            await asyncio.sleep(5)

            # Check verification result
            verified = False
            for sel in [
                "div:has-text('Verified')", "span:has-text('Verified')",
                "text=Verified", "[data-status='verified']",
                "div:has-text('success')",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        verified = True
                        _emit("info", "✓ 网站验证成功!", "verify_website")
                        break
                except Exception:
                    pass
            if not verified:
                _emit("info", "验证标签已注入，Google 将在后台完成验证", "verify_website")

            await helpers.click_continue()

            # --- Step 8: Select target country ---
            await _enter_step("select_country", f"步骤 8: 选择目标国家 — {country_name}")
            if not await helpers.fill_select([
                "mwc-select[label*='target country']", "mwc-select[label*='sales country']",
                "select[name='targetCountry']", "select[name='country']",
                "div[role='combobox'][aria-label*='target country']",
            ], country_name):
                _emit("info", "未找到目标国家选择器，跳过", "select_country")
            await helpers.click_continue()

            # --- Step 9: Add feed URL ---
            await _enter_step("add_feed", f"步骤 9: 添加产品 Feed — {feed_url}" if feed_url else "步骤 9: 无 Feed URL，跳过")
            if feed_url:
                if not await helpers.fill_any([
                    "mwc-textfield[label*='feed']", "mwc-textfield[label*='data source']",
                    "input[name='feedUrl']", "input[name='feed']", "input[name='url']",
                    "input[aria-label*='feed URL']", "input[aria-label*='data source']",
                    "input[placeholder*='xml']", "input[placeholder*='feed']",
                ], feed_url):
                    _emit("warning", "未找到 Feed URL 输入框", "add_feed")
                await helpers.click_continue()

            # --- Step 10: Shipping and delivery ---
            await _enter_step("shipping", "步骤 10: 运输和送货设置...")
            # Usually pre-filled based on business country; just continue
            await helpers.click_continue()

            # --- Step 11: Returns & refunds ---
            await _enter_step("returns", "步骤 11: 退货/退款政策...")

            rp_url = return_policy_url or f"https://{site_domain}/return-policy/"
            if not await helpers.fill_any([
                "mwc-textfield[label*='return policy']", "mwc-textfield[label*='policy URL']",
                "mwc-textfield[label*='returns']", "input[name='returnPolicyUrl']",
                "input[name='returnPolicy']", "input[aria-label*='return policy']",
            ], rp_url):
                _emit("info", "未找到退货政策 URL 输入框", "returns")

            # Select 30-day return window
            await helpers.fill_select([
                "mwc-select[label*='return window']", "mwc-select[label*='return period']",
                "select[name='returnWindow']", "div[role='combobox'][aria-label*='return window']",
            ], "30 days")

            # No restocking fee
            await helpers.click_any([
                "mwc-radio[value='no']", "div[role='radio']:has-text('No')",
                "label:has-text('No') input[type='radio']",
            ])

            await helpers.click_continue()

            # --- Step 12: Complete / Claim ---
            await _enter_step("complete", "步骤 12: 申领完成...")
            await helpers.click_any([
                "mwc-button:has-text('Complete')", "mwc-button:has-text('Finish')",
                "mwc-button:has-text('Claim')", "mwc-button:has-text('Done')",
                "button:has-text('Complete')", "button:has-text('Finish')",
                "button:has-text('Claim')", "button:has-text('Done')",
            ])
            await asyncio.sleep(5)

            # Extract MC account ID
            await _enter_step("extract_id", "提取 MC 账号 ID...")
            mc_id = ""
            m = _re.search(r'/a/(\d+)', page.url)
            if not m:
                m = _re.search(r'/mc/(\d+)', page.url)
            if not m:
                m = _re.search(r'merchant[_-]?id[=:]?\s*(\d+)', await page.content(), _re.I)
            if not m:
                for sel in [
                    "[data-merchant-id]", "span.merchant-id",
                    "div:has-text('Merchant ID') span",
                ]:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            text = await el.inner_text()
                            m = _re.search(r'\d{6,}', text)
                            if m:
                                break
                    except Exception:
                        pass
            if m:
                mc_id = m.group(1)
                result["mc_account_id"] = mc_id
                _emit("info", f"MC ID: {mc_id}", "extract_id")

            result["success"] = True
            result["message"] = f"GMC Next 注册完成 (MC ID: {mc_id})" if mc_id else "GMC Next 注册完成"
            _emit("info", f"✓ {result['message']}", "done")


        if not result.get("wizard_skipped"):
            await _run_wizard()
        mc_id = result.get("mc_account_id", mc_id)

        # ================================================================
        # Post-registration: GMC Setup flow (6-step setup wizard)
        # ================================================================
        if mc_id:
            _emit("info", "=" * 40)
            _emit("info", f"继续 GMC 设置向导 — MC ID: {mc_id}", "setup_start")

            # Try to navigate to the setup flow
            # GMC Next setup URL format: /mc/setup/products?a=...&flow=onlineOnboarding
            setup_url = f"https://merchants.google.com/mc/setup/products?a={mc_id}&flow=onlineOnboarding"
            _emit("info", f"导航到设置向导: {setup_url}", "setup_navigate")
            try:
                await page.goto(setup_url, wait_until="domcontentloaded", timeout=180000)
            except Exception:
                _emit("info", "直接导航失败，尝试在仪表盘中寻找设置入口...", "setup_navigate")
                # Fallback: stay on current page and try clicking setup-related links
                pass
            await asyncio.sleep(5)
            await _dismiss_overlays(page)

            # Walk through setup steps
            setup_step = 0
            prev_url = ""
            max_setup_steps = 15
            for _ in range(max_setup_steps):
                setup_step += 1
                step_name = f"gmc_setup_{setup_step:02d}"
                helpers.set_step(step_name)
                _emit("info", f"--- 设置步骤 {setup_step} ---", step_name)
                await asyncio.sleep(2)

                current_url = page.url
                if current_url == prev_url:
                    _emit("info", f"URL 未变化，可能已达到终点", step_name)
                    # Still dump to capture final state
                    if recon_dir:
                        await _dump_step_dom(page, recon_dir, step_name)
                    break
                prev_url = current_url

                if recon_dir:
                    await _dump_step_dom(page, recon_dir, step_name)

                # Try to click Continue/Next/Save to advance
                clicked = False
                for sel in [
                    "mwc-button:has-text('Continue')", "mwc-button:has-text('Next')",
                    "mwc-button:has-text('Save')", "mwc-button:has-text('Submit')",
                    "mwc-button:has-text('Done')", "mwc-button:has-text('Complete')",
                    "mwc-button:has-text('Finish')", "mwc-button:has-text('Confirm')",
                    "button:has-text('Continue')", "button:has-text('Next')",
                    "button:has-text('Save')", "button:has-text('Save and continue')",
                    "button:has-text('Submit')", "button:has-text('Done')",
                    "button:has-text('Complete')", "button:has-text('Finish')",
                    "[role='button']:has-text('Continue')",
                    "[role='button']:has-text('Next')",
                ]:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0 and await el.is_visible():
                            if await el.is_enabled():
                                await el.click(timeout=5000)
                                _emit("info", f"点击了: {sel}", step_name)
                                clicked = True
                                await asyncio.sleep(4)
                                break
                    except Exception:
                        continue

                if not clicked:
                    # Try any visible button that looks like a primary action
                    try:
                        btns = page.locator("button:visible")
                        count = await btns.count()
                        for i in range(min(count, 30)):
                            btn = btns.nth(i)
                            text = (await btn.inner_text()).strip().lower()
                            if any(kw in text for kw in ["continue", "next", "save", "submit", "done", "finish", "confirm", "accept", "agree", "get started", "start"]):
                                if await btn.is_enabled():
                                    await btn.click(timeout=5000)
                                    _emit("info", f"点击了可见按钮: '{text}'", step_name)
                                    clicked = True
                                    await asyncio.sleep(4)
                                    break
                    except Exception:
                        pass

                if not clicked:
                    _emit("info", "未找到可点击的继续按钮，设置流程可能已完成", step_name)
                    break

            _emit("info", f"GMC 设置向导侦察完成，共捕获 {setup_step} 个步骤", "setup_done")
            result["setup_steps_captured"] = setup_step

    except StepFailed:
        result["success"] = False
    except Exception as e:
        result["message"] = f"步骤 '{result['step']}' 失败: {e}"
        _emit("error", f"步骤 '{result['step']}' 异常: {e}", result["step"])

    finally:
        if browser_ctx:
            try:
                await browser_ctx.close()
            except Exception:
                pass

    return result


async def _launch_with_playwright_proxy(
    profile_dir: str,
    fingerprint_args: list,
    timezone: str,
    locale: str,
    proxy_url: str,
    headless: bool = True,
):
    """Launch browser via raw Playwright with proxy dict for proper HTTP proxy auth.

    CloakBrowser's inline auth (--proxy-server with creds) doesn't handle
    HTTP proxy auth negotiation correctly. Playwright's native proxy dict
    uses CDP Fetch.authChallengeResponse to handle 407 challenges, which
    works for both successful auth and error responses like 403.
    """
    from urllib.parse import urlparse, unquote
    from playwright.async_api import async_playwright
    from cloakbrowser import ensure_binary, build_args

    parsed = urlparse(proxy_url)
    proxy_dict = {
        "server": f"http://{parsed.hostname}:{parsed.port or 80}",
        "username": unquote(parsed.username) if parsed.username else "",
        "password": unquote(parsed.password) if parsed.password else "",
    }
    logger.info("Playwright proxy dict: server=%s username=%s", proxy_dict["server"], proxy_dict["username"])

    binary_path = ensure_binary()
    chrome_args = build_args(
        True,  # stealth_args
        fingerprint_args,
        timezone=timezone,
        locale=locale,
        headless=headless,
    )

    pw = await async_playwright().start()
    try:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            executable_path=binary_path,
            headless=headless,
            args=chrome_args,
            proxy=proxy_dict,
            ignore_default_args=["--enable-automation", "--enable-unsafe-swiftshader"],
        )
    except Exception:
        await pw.stop()
        raise

    # Patch close() to also stop Playwright
    _orig_close = context.close

    async def _close_with_cleanup():
        try:
            await _orig_close()
        finally:
            await pw.stop()

    context.close = _close_with_cleanup
    return context


async def auto_verify_google_site(
    profile_dir: str,
    site_domain: str = "",
    wp_url: str = "",
    wp_username: str = "",
    wp_password: str = "",
    google_email: str = "",
    google_password: str = "",
    google_totp_secret: str = "",
    headless: bool = True,
    timeout_ms: int = 180000,  # slow proxies need generous timeout
    test_only: bool = False,
    log_callback=None,
) -> dict:
    """自动完成 Google 站点验证（meta tag 方式）。

    用 CloakBrowser 打开 GMC → 提取验证码 → 注入 WordPress → 点击验证。

    test_only=True 时只检查浏览器是否可启动 + Google 是否已登录，随后立即返回。
    log_callback(level, message, step) — 可选，用于实时日志推送到前端。
    """
    import traceback

    def _emit(level: str, msg: str, step: str = ""):
        """同时输出到 Python logger 和前端 log_callback。"""
        log_func = {"info": logger.info, "warning": logger.warning, "error": logger.error}.get(level, logger.info)
        log_func("%s", msg)
        if log_callback:
            try:
                log_callback(level, msg, step)
            except Exception:
                pass

    _emit("info", "=" * 50)
    _emit("info", f"AutoVerify 开始 — site={site_domain} profile={os.path.basename(profile_dir)}")
    _emit("info", f"参数: headless={headless} timeout={timeout_ms/1000}s test_only={test_only}")

    try:
        from cloakbrowser import launch_persistent_context_async
    except ImportError as e:
        _emit("error", f"cloakbrowser 导入失败: {e}", "import")
        return {"success": False, "message": "cloakbrowser 未安装", "step": "import"}

    if not os.path.isdir(profile_dir):
        _emit("error", f"Profile 目录不存在: {profile_dir}", "profile_dir")
        return {"success": False, "message": f"Profile 目录不存在: {profile_dir}", "step": "profile_dir"}

    _emit("info", "正在加载 profile 配置...", "config")
    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
    tz = config.get("timezone", "America/Chicago")
    locale = config.get("locale", "en-US")
    fingerprint_args = _build_launch_args(config)
    _emit("info", f"配置加载完成 — platform={config.get('platform','?')} proxy={proxy[:40]+'...' if len(proxy)>40 else proxy or '(无)'} tz={tz} locale={locale}", "config")

    browser_ctx = None
    result = {"success": False, "verification_code": "", "step": ""}

    try:
        launch_kwargs = {
            "headless": headless,
            "timezone": tz,
            "locale": locale,
            "args": fingerprint_args,
            "humanize": True,
            "stealth_args": False,
        }
        if proxy:
            launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)
            launch_kwargs["geoip"] = True

        if test_only and proxy and proxy.startswith("http://"):
            # HTTP proxy test: use raw Playwright with CDP auth.
            # Chromium's native --proxy-server with inline creds
            # (used by CloakBrowser) breaks for HTTP proxy auth.
            # Playwright's Fetch.authChallengeResponse CDP handling
            # properly negotiates 407 → auth → receives actual response.
            logger.info("test_only HTTP proxy: launching via raw Playwright + CDP auth")
            _emit("info", "正在启动浏览器 (Playwright CDP 代理模式)...", "launch")
            try:
                browser_ctx = await _launch_with_playwright_proxy(
                    profile_dir, fingerprint_args, tz, locale, proxy, headless
                )
            except Exception as e:
                _emit("error", f"浏览器启动失败: {e}", "launch")
                result["step"] = "launch"
                result["message"] = f"浏览器启动失败: {e}"
                return result
        else:
            _emit("info", f"正在启动浏览器... (headless={headless})", "launch")
            t_launch_start = asyncio.get_event_loop().time()
            try:
                browser_ctx = await launch_persistent_context_async(profile_dir, **launch_kwargs)
            except Exception as e:
                _emit("error", f"浏览器启动失败: {e}", "launch")
                result["step"] = "launch"
                result["message"] = f"浏览器启动失败: {e}"
                return result
            _emit("info", f"浏览器启动成功 (耗时 {asyncio.get_event_loop().time() - t_launch_start:.1f}s)", "launch")

        if browser_ctx.pages:
            page = browser_ctx.pages[0]
        else:
            page = await browser_ctx.new_page()
        page.set_default_timeout(timeout_ms)
        _emit("info", f"页面就绪，默认超时={timeout_ms/1000}s", "launch")

        # --- test_only: just verify browser + proxy works ---
        if test_only:
            _emit("info", "test_only 模式 — 检查网络连通性", "test")
            result["step"] = "launch"
            try:
                _emit("info", "访问 httpbin.org/ip ...", "test")
                resp = await page.goto("http://httpbin.org/ip", wait_until="domcontentloaded", timeout=20000)
                _emit("info", f"httpbin 响应状态={resp.status if resp else 'no-response'} url={page.url}", "test")
                await asyncio.sleep(1)
                body = await page.inner_text("body")
                ip_info = body.strip() if body else "(empty body)"
                _emit("info", f"出口 IP: {ip_info}", "test")
                return {"success": True, "message": f"Profile 可用，出口IP: {ip_info}", "ip": ip_info}
            except Exception as e:
                _emit("error", f"网络连通性测试失败: {e}", "test")
                result["step"] = "httpbin"
                result["message"] = f"网络连通性测试失败: {e}"
                return result

        # --- Check Google login ---
        result["step"] = "google_login"
        _emit("info", "步骤 1: 检查 Google 登录状态 — 访问 merchants.google.com ...", "google_login")
        t_step = asyncio.get_event_loop().time()
        try:
            resp = await page.goto("https://merchants.google.com/", wait_until="domcontentloaded", timeout=180000)
            _emit("info", f"GMC 首页响应 status={resp.status if resp else 'no-response'} (耗时 {asyncio.get_event_loop().time() - t_step:.1f}s)", "google_login")
        except Exception as e:
            _emit("error", f"访问 GMC 首页超时/失败: {e}", "google_login")
            result["message"] = f"访问 Google Merchant Center 失败(网络超时): {e}"
            return result
        await asyncio.sleep(3)

        if "accounts.google.com" in page.url:
            _emit("warning", f"Google 未登录 — 当前 URL: {page.url[:80]}", "google_login")
            if google_email and google_password:
                _emit("info", f"检测到 Google 账户凭据，尝试自动登录...", "google_login")
                login_ok = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=timeout_ms,
                )
                if login_ok:
                    _emit("info", "Google 自动登录成功，继续站点验证流程", "google_login")
                    await page.goto("https://merchants.google.com/mc/settings/website", wait_until="domcontentloaded", timeout=180000)
                    await asyncio.sleep(3)
                else:
                    _emit("error", "Google 自动登录失败", "google_login")
                    return {
                        "success": False,
                        "message": "Google 自动登录失败，请检查账户凭据或 2FA 密钥",
                        "step": "google_login",
                    }
            else:
                return {
                    "success": False,
                    "message": f"Google 未登录。请先在 headless=False 下打开 profile '{os.path.basename(profile_dir)}' 并手动登录，或在品牌套件中关联 Google 账户",
                    "step": "google_login",
                }
        _emit("info", f"Google 已登录 — 当前 URL: {page.url[:80]}", "google_login")

        # If brand kit specifies a Google account, ensure correct account is logged in
        if google_email:
            logged_in = await _get_logged_in_email(page)
            if logged_in and google_email.lower() != logged_in.lower():
                _emit("warning", f"Profile 当前登录 {logged_in}，但品牌套件指定 {google_email}，正在切换账户...", "google_login")
                # Sign out
                await page.goto("https://accounts.google.com/Logout", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                # Re-login with brand kit account
                login_ok = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=timeout_ms,
                )
                if login_ok:
                    _emit("info", f"已切换到品牌套件账户: {google_email}", "google_login")
                else:
                    _emit("error", f"切换账户失败: {google_email}", "google_login")
                    return {
                        "success": False,
                        "message": f"切换到 Google 账户 {google_email} 失败，请检查凭据",
                        "step": "google_login",
                    }

        # --- Navigate to Business Info → Website (GMC Next SPA) ---
        result["step"] = "navigate_settings"
        _emit("info", "步骤 2: 导航到 Business Information → Website ...", "navigate_settings")

        # GMC Next: navigate via sidebar "Business information" → "Website" tab
        biz_clicked = False
        for sel in [
            "a[href*='business']", "a:has-text('Business information')",
            "span:has-text('Business information')", "mwc-list-item:has-text('Business info')",
            "[data-nav='business-information']", "nav a:has-text('Business')",
            ".nav-item:has-text('Business')", "a:has-text('Settings')",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(3)
                    biz_clicked = True
                    _emit("info", f"点击了 Business Information: {sel[:50]}", "navigate_settings")
                    break
            except Exception:
                pass
        if not biz_clicked:
            # Fallback: try direct URL (may still work in some accounts)
            try:
                await page.goto("https://merchants.google.com/mc/settings/website", wait_until="domcontentloaded", timeout=180000)
                _emit("info", "使用旧版 URL 导航到验证设置页", "navigate_settings")
            except Exception:
                pass
        await asyncio.sleep(2)

        # Click Website tab if visible
        for sel in [
            "div[role='tab']:has-text('Website')", "button[role='tab']:has-text('Website')",
            "mwc-tab:has-text('Website')", "a:has-text('Your website')",
            "a:has-text('Website')",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await asyncio.sleep(2)
                    _emit("info", f"点击了 Website 标签: {sel[:50]}", "navigate_settings")
                    break
            except Exception:
                pass

        # Check if already verified
        for sel in ["text=Verified", "text=verified", "[aria-label*='Verified']",
                     "div:has-text('Your website is verified')",
                     "span:has-text('Verified')", "div.status-verified"]:
            try:
                if await page.locator(sel).first.is_visible():
                    _emit("info", "网站已验证 (already verified)", "navigate_settings")
                    result["success"] = True
                    result["message"] = "网站已验证"
                    result["already_verified"] = True
                    return result
            except Exception:
                pass
        _emit("info", "网站尚未验证，继续流程...", "navigate_settings")

        # --- Enter website URL if needed ---
        result["step"] = "enter_url"
        _emit("info", f"步骤 3: 输入网站 URL: https://{site_domain}", "enter_url")
        helpers = _make_step_helpers(page, _emit, timeout_ms)
        if not await helpers.fill_any([
            "mwc-textfield[label*='website']", "mwc-textfield[label*='store']",
            "input[name='websiteUrl']", "input[aria-label*='Website']",
            "input[aria-label*='website']", "input[type='url']",
        ], f"https://{site_domain}"):
            _emit("info", "未找到网站 URL 输入框，可能已自动填充", "enter_url")
        await helpers.click_continue()

        # --- Select HTML tag method ---
        result["step"] = "select_method"
        _emit("info", "步骤 4: 选择 HTML 标签验证方式...", "select_method")
        if not await helpers.click_any([
            "mwc-radio[value*='tag']", "mwc-radio[value*='meta']",
            "mwc-radio[value*='html']", "div[role='radio']:has-text('HTML tag')",
            "label:has-text('HTML tag') input[type='radio']",
            "span:has-text('Add an HTML tag')",
        ]):
            _emit("info", "未找到 HTML 标签选项，可能已默认选中", "select_method")

        # --- Extract verification code ---
        result["step"] = "extract_code"
        _emit("info", "步骤 5: 提取验证码...", "extract_code")
        verification_code = ""
        # Try dedicated elements first
        for sel in ["code", "pre", "[data-code]", "[data-value]",
                     "span:has-text('content=')", "div:has-text('google-site-verification')"]:
            el = page.locator(sel)
            if await el.count() > 0:
                text = await el.first.inner_text()
                _emit("info", f"从元素 '{sel[:30]}' 提取文本: {text[:100]}", "extract_code")
                m = _re.search(r'content=["\']([^"\']+)["\']', text)
                if m:
                    verification_code = m.group(1)
                    break
                m2 = _re.search(r'google-site-verification[:\s]*([a-zA-Z0-9_-]{20,})', text)
                if m2:
                    verification_code = m2.group(1)
                    break

        # Fallback: scan full page HTML
        if not verification_code:
            _emit("info", "专用元素未找到验证码，扫描完整页面 HTML...", "extract_code")
            html = await page.content()
            m = _re.search(r'content=["\']([a-zA-Z0-9_-]{20,})["\']', html)
            if m:
                verification_code = m.group(1)
                _emit("info", f"从页面 HTML 提取到验证码: {verification_code[:16]}...", "extract_code")

        if not verification_code:
            _emit("error", "无法从页面提取验证码 — 页面可能尚未加载验证标签区域", "extract_code")
            return {"success": False, "message": "无法从页面提取验证码", "step": "extract_code"}

        result["verification_code"] = verification_code
        _emit("info", f"验证码提取成功: {verification_code[:16]}...", "extract_code")

        # --- Inject into WordPress ---
        if wp_url and wp_username and wp_password:
            result["step"] = "inject_wp"
            _emit("info", f"步骤 6: 注入验证标签到 WordPress ({wp_url})...", "inject_wp")
            try:
                from services.wordpress_client import WordPressAdminSession
                wp = WordPressAdminSession(wp_url, wp_username, wp_password)
                inject_res = wp.inject_google_verification(verification_code, "meta")
                if inject_res.get("success"):
                    _emit("info", "验证标签已成功注入 WordPress", "inject_wp")
                else:
                    _emit("warning", f"WordPress 注入警告: {inject_res.get('message', '')}", "inject_wp")
            except Exception as e:
                _emit("warning", f"WordPress 注入异常: {e}", "inject_wp")
        else:
            _emit("info", f"跳过 WP 注入 (wp_url={'✓' if wp_url else '✗'} wp_user={'✓' if wp_username else '✗'})", "inject_wp")

        # --- Wait for Google to detect the tag ---
        _emit("info", "步骤 7: 等待 10 秒，让 Google 检测验证标签...", "verify_click")
        await asyncio.sleep(10)

        # --- Click Verify ---
        result["step"] = "verify_click"
        verified = False
        for attempt in range(5):
            _emit("info", f"步骤 8: 点击验证按钮 (第 {attempt+1}/5 次)...", "verify_click")
            btn_found = False
            for sel in [
                "button:has-text('Verify')", "button:has-text('Confirm')",
                "button:has-text('Validate')", "button:has-text('I have added')",
                "button:has-text('Check')", "button:has-text('Done')",
                "button:has-text('Submit')",
            ]:
                btn = page.locator(sel).first
                try:
                    if await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(5)
                        btn_found = True
                        _emit("info", f"点击了验证按钮: {sel}", "verify_click")
                        break
                except Exception:
                    pass
            if not btn_found:
                _emit("warning", "未找到可见的验证按钮", "verify_click")
                # Take screenshot for debugging
                try:
                    await page.screenshot(path="/tmp/gmc_verify_debug.png")
                    _emit("info", "已保存调试截图到 /tmp/gmc_verify_debug.png", "verify_click")
                except Exception:
                    pass

            # Check success indicators
            for sel in ["text=Verified", "text=verified", "[aria-label*='Verified']",
                         "div:has-text('Your website is verified')",
                         "div:has-text('Success')"]:
                try:
                    if await page.locator(sel).first.is_visible():
                        verified = True
                        _emit("info", "检测到验证成功标志!", "verify_click")
                        break
                except Exception:
                    pass
            if verified:
                break
            if attempt < 4:
                _emit("info", f"验证尚未成功，等待 5 秒后重试 ({attempt+1}/5)...", "verify_click")
                await asyncio.sleep(5)

        if verified:
            result["success"] = True
            result["message"] = "网站验证成功"
            _emit("info", "✓ 网站验证成功!", "done")
        else:
            result["success"] = False  # Honestly report failure
            result["message"] = "验证标签已注入 WordPress，但 Google 端验证按钮未找到/未完成，请手动检查 GMC"
            _emit("warning", "验证标签已注入 WordPress，但 Google 端未能自动完成验证", "done")

    except Exception as e:
        result["message"] = f"步骤 '{result['step']}' 失败: {e}"
        _emit("error", f"步骤 '{result['step']}' 异常: {e}", result["step"])

    finally:
        if browser_ctx:
            try:
                await browser_ctx.close()
            except Exception:
                pass

    return result


async def gmc_recon(
    profile_dir: str,
    google_email: str = "",
    google_password: str = "",
    google_totp_secret: str = "",
    onboarding_url: str = "",
    headless: bool = False,
    timeout_ms: int = 180000,  # slow proxies need generous timeout
    log_callback=None,
) -> dict:
    """GMC Next 流程侦查：打开 GMC Next 注册流程，遍历每一步并导出 DOM 结构 + 截图。

    不填写任何值，只点 Continue 推进步骤，把每步看到的表单元素记录下来。
    输出目录: /tmp/gmc_recon/（screenshots + dom dumps）
    """
    import json as _json

    out_dir = "/tmp/gmc_recon"
    os.makedirs(out_dir, exist_ok=True)

    def _emit(level: str, msg: str, step: str = ""):
        log_func = {"info": logger.info, "warning": logger.warning, "error": logger.error}.get(level, logger.info)
        log_func("%s", msg)
        if log_callback:
            try:
                log_callback(level, msg, step)
            except Exception:
                pass

    _emit("info", "=" * 50)
    _emit("info", f"GMC 侦查模式 — profile={os.path.basename(profile_dir)}")

    try:
        from cloakbrowser import launch_persistent_context_async
    except ImportError:
        return {"success": False, "message": "cloakbrowser 未安装"}

    if not os.path.isdir(profile_dir):
        return {"success": False, "message": f"Profile 目录不存在: {profile_dir}"}

    config = load_profile_config(profile_dir) or {}
    proxy = (config.get("proxy", "") or "").replace("socks5h://", "socks5://")
    tz = config.get("timezone", "America/Chicago")
    locale = config.get("locale", "en-US")
    fingerprint_args = _build_launch_args(config)

    browser_ctx = None
    steps_captured = []

    try:
        launch_kwargs = {
            "headless": headless,
            "timezone": tz,
            "locale": locale,
            "args": fingerprint_args,
            "humanize": True,
            "stealth_args": False,
        }
        if proxy:
            launch_kwargs["proxy"] = _normalize_proxy_for_launch(proxy)
            launch_kwargs["geoip"] = True

        _emit("info", f"启动浏览器... (headless={headless})", "launch")
        browser_ctx = await launch_persistent_context_async(profile_dir, **launch_kwargs)
        page = browser_ctx.pages[0] if browser_ctx.pages else await browser_ctx.new_page()
        page.set_default_timeout(timeout_ms)
        _emit("info", "浏览器已启动", "launch")

        # Navigate
        target_url = onboarding_url or "https://merchants.google.com/"
        _emit("info", f"导航到: {target_url}", "navigate")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=180000)
        await asyncio.sleep(5)

        # If redirected to login, do login
        if "accounts.google.com" in page.url:
            _emit("warning", "需要登录 Google", "login")
            if google_email and google_password:
                login_ok = await _google_login(
                    page, google_email, google_password, google_totp_secret,
                    log_callback=log_callback, timeout_ms=timeout_ms,
                )
                if login_ok:
                    _emit("info", "登录成功，返回 GMC...", "login")
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=180000)
                    await asyncio.sleep(5)
                else:
                    return {"success": False, "message": "Google 登录失败"}

        # Dismiss overlays
        await _dismiss_overlays(page)
        await asyncio.sleep(2)

        # If we're on a generic page (not onboarding), and no onboarding URL provided,
        # try to start a new onboarding or navigate to setup
        current_url = page.url
        _emit("info", f"当前 URL: {current_url[:120]}", "navigate")

        # Now walk through steps, dumping DOM at each
        max_steps = 20
        prev_url = ""
        help_messages_seen = set()

        for step_num in range(1, max_steps + 1):
            _emit("info", f"--- 侦查步骤 {step_num} ---", f"recon_step_{step_num}")
            await asyncio.sleep(2)

            current_url = page.url
            if current_url == prev_url and step_num > 1:
                _emit("info", f"URL 未变化，可能已到达终点或卡住", f"recon_step_{step_num}")
                # Still try to dump what we see

            prev_url = current_url

            # Take screenshot
            ss_path = os.path.join(out_dir, f"step_{step_num:02d}.png")
            try:
                await page.screenshot(path=ss_path, full_page=False)
                _emit("info", f"截图已保存: {ss_path}", f"recon_step_{step_num}")
            except Exception as e:
                _emit("warning", f"截图失败: {e}", f"recon_step_{step_num}")

            # Dump DOM structure
            dom_info = await page.evaluate("""() => {
                const result = {
                    url: location.href,
                    title: document.title,
                    step: '',
                    forms: [],
                    inputs: [],
                    selects: [],
                    textareas: [],
                    buttons: [],
                    radios: [],
                    checkboxes: [],
                    links: [],
                    headings: [],
                    mwcElements: [],
                    ariaLabels: [],
                    helpTexts: [],
                    errorTexts: [],
                };

                // Try to detect step indicator
                const stepIndicators = document.querySelectorAll(
                    '[data-step], [aria-current="step"], .step-indicator, .stepper, ' +
                    'mat-step-header, mwc-tab-bar, [role="tablist"], .progress-indicator'
                );
                stepIndicators.forEach(el => {
                    const text = el.textContent?.trim()?.substring(0, 200);
                    if (text) result.step += text + ' | ';
                });

                // Forms
                document.querySelectorAll('form').forEach(el => {
                    result.forms.push({
                        id: el.id, name: el.name, action: el.action?.substring(0, 100),
                        method: el.method, visible: el.offsetParent !== null,
                        childCount: el.querySelectorAll('input, select, textarea, button').length
                    });
                });

                // Inputs (visible only)
                document.querySelectorAll('input:not([type="hidden"])').forEach(el => {
                    if (!el.offsetParent) return;
                    const label = el.closest('label')?.textContent?.trim()?.substring(0, 80) || '';
                    const parentLabel = el.parentElement?.textContent?.trim()?.substring(0, 80) || '';
                    const wrapper = el.closest('[class*="field"], [class*="input"], [class*="form"]');
                    const wrapperText = wrapper?.textContent?.trim()?.substring(0, 100) || '';
                    result.inputs.push({
                        type: el.type, name: el.name, id: el.id,
                        placeholder: el.placeholder, value: el.value?.substring(0, 50),
                        ariaLabel: el.getAttribute('aria-label'),
                        required: el.required,
                        className: el.className?.substring(0, 100),
                        label: label, parentLabel: parentLabel,
                        wrapperText: wrapperText.replace(/\\s+/g, ' ').substring(0, 150),
                    });
                });

                // Selects
                document.querySelectorAll('select').forEach(el => {
                    if (!el.offsetParent) return;
                    result.selects.push({
                        name: el.name, id: el.id,
                        ariaLabel: el.getAttribute('aria-label'),
                        options: Array.from(el.options).slice(0, 20).map(o => o.text?.trim()),
                    });
                });

                // Textareas
                document.querySelectorAll('textarea').forEach(el => {
                    if (!el.offsetParent) return;
                    result.textareas.push({
                        name: el.name, id: el.id, placeholder: el.placeholder,
                        ariaLabel: el.getAttribute('aria-label'),
                    });
                });

                // Buttons
                document.querySelectorAll('button, [role="button"], a[role="button"]').forEach(el => {
                    if (!el.offsetParent) return;
                    result.buttons.push({
                        text: el.textContent?.trim()?.substring(0, 100),
                        type: el.type, name: el.name, id: el.id,
                        ariaLabel: el.getAttribute('aria-label'),
                        disabled: el.disabled,
                        className: el.className?.substring(0, 100),
                    });
                });

                // Radio groups
                document.querySelectorAll('input[type="radio"]').forEach(el => {
                    const label = el.closest('label')?.textContent?.trim()?.substring(0, 80);
                    const parentText = el.parentElement?.textContent?.trim()?.substring(0, 80);
                    result.radios.push({
                        name: el.name, value: el.value, checked: el.checked,
                        label: label || parentText, visible: el.offsetParent !== null,
                    });
                });
                // Also catch div/span radios
                document.querySelectorAll('[role="radio"]').forEach(el => {
                    if (!el.offsetParent) return;
                    result.radios.push({
                        role: 'radio', text: el.textContent?.trim()?.substring(0, 100),
                        checked: el.getAttribute('aria-checked'),
                    });
                });

                // Checkboxes
                document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                    const label = el.closest('label')?.textContent?.trim()?.substring(0, 80);
                    result.checkboxes.push({
                        name: el.name, value: el.value, checked: el.checked,
                        label: label, visible: el.offsetParent !== null,
                    });
                });

                // MWC elements
                document.querySelectorAll('mwc-textfield, mwc-select, mwc-button, ' +
                    'mwc-radio, mwc-checkbox, mwc-textarea').forEach(el => {
                    const attrs = {};
                    for (const a of el.attributes) {
                        attrs[a.name] = a.value?.substring(0, 100);
                    }
                    result.mwcElements.push({
                        tag: el.tagName.toLowerCase(),
                        label: el.getAttribute('label') || el.getAttribute('aria-label'),
                        value: el.getAttribute('value'),
                        disabled: el.hasAttribute('disabled'),
                        attributes: attrs,
                    });
                });

                // Elements with aria-label
                document.querySelectorAll('[aria-label]').forEach(el => {
                    if (!el.offsetParent) return;
                    const aria = el.getAttribute('aria-label');
                    if (aria && aria.length > 2 && aria.length < 200) {
                        result.ariaLabels.push({
                            tag: el.tagName.toLowerCase(),
                            ariaLabel: aria,
                            text: el.textContent?.trim()?.substring(0, 80),
                        });
                    }
                });

                // Headings
                document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
                    if (!el.offsetParent) return;
                    result.headings.push({
                        tag: el.tagName,
                        text: el.textContent?.trim()?.substring(0, 200),
                    });
                });

                // Help/description text (elements with hint/helper/description classes)
                document.querySelectorAll('[class*="help"], [class*="hint"], ' +
                    '[class*="helper"], [class*="description"], [class*="supporting"], ' +
                    '[class*="secondary"], [class*="subtitle"]').forEach(el => {
                    if (!el.offsetParent) return;
                    const t = el.textContent?.trim();
                    if (t && t.length > 2 && t.length < 500) {
                        result.helpTexts.push(t);
                    }
                });

                // Error messages
                document.querySelectorAll('[class*="error"], [class*="Error"], ' +
                    '[role="alert"], [aria-invalid="true"] ~ [class*="error"]').forEach(el => {
                    if (!el.offsetParent) return;
                    const t = el.textContent?.trim();
                    if (t && t.length > 1) result.errorTexts.push(t);
                });

                return JSON.parse(JSON.stringify(result));
            }""")

            # Save DOM dump as JSON
            dom_path = os.path.join(out_dir, f"step_{step_num:02d}.json")
            with open(dom_path, "w", encoding="utf-8") as f:
                _json.dump(dom_info, f, ensure_ascii=False, indent=2)
            _emit("info", f"DOM 结构已保存: {dom_path}", f"recon_step_{step_num}")

            steps_captured.append({
                "step": step_num,
                "url": dom_info.get("url", ""),
                "title": dom_info.get("title", ""),
                "headings": [h["text"] for h in dom_info.get("headings", [])[:5]],
                "num_inputs": len(dom_info.get("inputs", [])),
                "num_buttons": len(dom_info.get("buttons", [])),
                "num_mwc": len(dom_info.get("mwcElements", [])),
                "screenshot": ss_path,
                "dom_dump": dom_path,
            })

            # Try clicking Continue / Next to go to next step
            clicked = False
            for sel in [
                "mwc-button:has-text('Continue')",
                "mwc-button:has-text('Next')",
                "mwc-button:has-text('Save')",
                "mwc-button:has-text('Submit')",
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button:has-text('Save and continue')",
                "button[type='submit']",
                "[role='button']:has-text('Continue')",
                "[role='button']:has-text('Next')",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        if await el.is_enabled():
                            await el.click(timeout=5000)
                            _emit("info", f"点击了: {sel}", f"recon_step_{step_num}")
                            clicked = True
                            await asyncio.sleep(4)
                            break
                except Exception:
                    continue

            if not clicked:
                # Try less strict match
                try:
                    btns = page.locator("button:visible")
                    count = await btns.count()
                    found_btn = None
                    for i in range(min(count, 20)):
                        btn = btns.nth(i)
                        text = (await btn.inner_text()).strip().lower()
                        if any(kw in text for kw in ["continue", "next", "save", "submit", "done"]):
                            if await btn.is_enabled():
                                found_btn = text
                                await btn.click(timeout=5000)
                                _emit("info", f"点击了可见按钮: '{found_btn}'", f"recon_step_{step_num}")
                                clicked = True
                                await asyncio.sleep(4)
                                break
                except Exception:
                    pass

            if not clicked:
                _emit("info", "未找到 Continue/Next 按钮，流程可能已结束", f"recon_step_{step_num}")
                break

        _emit("info", f"侦查完成，共捕获 {len(steps_captured)} 个步骤", "done")
        return {
            "success": True,
            "message": f"侦查完成，共 {len(steps_captured)} 个步骤",
            "output_dir": out_dir,
            "steps": steps_captured,
        }

    except Exception as e:
        _emit("error", f"侦查异常: {e}", "error")
        return {"success": False, "message": str(e), "steps": steps_captured}
    finally:
        if browser_ctx:
            try:
                await browser_ctx.close()
            except Exception:
                pass


def country_to_locale(country: str) -> str:
    """国家代码 → BCP 47 locale。"""
    return _REGION_CONFIGS.get(country, _REGION_CONFIGS["US"])["locale"]
