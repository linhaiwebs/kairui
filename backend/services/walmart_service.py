"""
CrawlbaseWalmartService — Fetch Walmart US bestseller product data via Crawlbase.

Usage::

    from services import CrawlbaseWalmartService
    svc = CrawlbaseWalmartService(token="...")
    products = svc.fetch_category_bestsellers("electronics", limit=50)

Integration with Flask app config::

    from flask import current_app
    svc = CrawlbaseWalmartService(token=current_app.config.get("CRAWLBASE_TOKEN"))
"""

import logging
import time
from typing import Any, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class WalmartServiceError(Exception):
    """Base exception for the Walmart service module."""


class CrawlbaseAuthError(WalmartServiceError):
    """403 – token invalid or quota exhausted."""


class CrawlbaseRateLimitError(WalmartServiceError):
    """429 – too many concurrent requests."""


class CrawlbaseParseError(WalmartServiceError):
    """422 – Crawlbase was unable to parse the target page."""


class CrawlbaseTimeoutError(WalmartServiceError):
    """Request timed out."""


# ---------------------------------------------------------------------------
# Category → Walmart bestseller URL mapping
# ---------------------------------------------------------------------------

# Category groups with sub-categories
# Uses browse URLs where Crawlbase autoparse works, search URLs as fallback
CATEGORY_GROUPS: list[dict] = [
    {
        "group": "电子设备",
        "items": [
            {"key": "electronics", "label": "全部电子设备",
             "url": "https://www.walmart.com/browse/electronics/3944?sort=best_seller"},
            {"key": "electronics_tv", "label": "电视及影音",
             "url": "https://www.walmart.com/browse/electronics/3944_1060825?sort=best_seller"},
            {"key": "electronics_computer", "label": "电脑及平板",
             "url": "https://www.walmart.com/browse/electronics/3944_3951?sort=best_seller"},
            {"key": "electronics_phone", "label": "手机",
             "url": "https://www.walmart.com/search?q=cell+phone&sort=best_seller"},
            {"key": "electronics_headphones", "label": "耳机",
             "url": "https://www.walmart.com/search?q=headphones&sort=best_seller"},
            {"key": "electronics_speaker", "label": "音箱",
             "url": "https://www.walmart.com/search?q=bluetooth+speaker&sort=best_seller"},
            {"key": "electronics_smarthome", "label": "智能家居",
             "url": "https://www.walmart.com/search?q=smart+home&sort=best_seller"},
            {"key": "electronics_wearable", "label": "穿戴设备",
             "url": "https://www.walmart.com/search?q=smartwatch&sort=best_seller"},
            {"key": "electronics_car", "label": "车载电子",
             "url": "https://www.walmart.com/search?q=car+electronics&sort=best_seller"},
            {"key": "electronics_gaming", "label": "游戏设备",
             "url": "https://www.walmart.com/search?q=video+games&sort=best_seller"},
        ],
    },
    {
        "group": "家具家居",
        "items": [
            {"key": "home_furniture", "label": "全部家具家居",
             "url": "https://www.walmart.com/browse/home/4044?sort=best_seller"},
            {"key": "home_furniture_item", "label": "家具",
             "url": "https://www.walmart.com/search?q=furniture&sort=best_seller"},
            {"key": "home_mattress", "label": "床垫",
             "url": "https://www.walmart.com/search?q=mattress&sort=best_seller"},
            {"key": "home_bedding", "label": "床上用品",
             "url": "https://www.walmart.com/search?q=bedding+set&sort=best_seller"},
            {"key": "home_kitchen", "label": "厨房餐厅",
             "url": "https://www.walmart.com/search?q=kitchen+dining&sort=best_seller"},
            {"key": "home_bath", "label": "卫浴用品",
             "url": "https://www.walmart.com/search?q=bath+towels&sort=best_seller"},
            {"key": "home_storage", "label": "收纳整理",
             "url": "https://www.walmart.com/search?q=storage+organization&sort=best_seller"},
            {"key": "home_lighting", "label": "灯具照明",
             "url": "https://www.walmart.com/search?q=lamp+lighting&sort=best_seller"},
            {"key": "home_decor", "label": "家居装饰",
             "url": "https://www.walmart.com/search?q=home+decor&sort=best_seller"},
        ],
    },
    {
        "group": "时尚服饰",
        "items": [
            {"key": "fashion", "label": "全部时尚服饰",
             "url": "https://www.walmart.com/browse/clothing/5438?sort=best_seller"},
            {"key": "fashion_women", "label": "女装",
             "url": "https://www.walmart.com/browse/clothing/5438_133197?sort=best_seller"},
            {"key": "fashion_men", "label": "男装",
             "url": "https://www.walmart.com/search?q=mens+clothing&sort=best_seller"},
            {"key": "fashion_shoes", "label": "鞋履",
             "url": "https://www.walmart.com/browse/clothing/5438_1045799?sort=best_seller"},
            {"key": "fashion_jewelry", "label": "珠宝首饰",
             "url": "https://www.walmart.com/search?q=jewelry&sort=best_seller"},
        ],
    },
    {
        "group": "宠物用品",
        "items": [
            {"key": "pet_supplies", "label": "全部宠物用品",
             "url": "https://www.walmart.com/browse/pets/5440?sort=best_seller"},
            {"key": "pet_dog", "label": "狗狗用品",
             "url": "https://www.walmart.com/search?q=dog+supplies&sort=best_seller"},
            {"key": "pet_cat", "label": "猫咪用品",
             "url": "https://www.walmart.com/search?q=cat+supplies&sort=best_seller"},
        ],
    },
    {
        "group": "食品饮料",
        "items": [
            {"key": "grocery", "label": "全部食品饮料",
             "url": "https://www.walmart.com/browse/food/976759?sort=best_seller"},
            {"key": "grocery_snacks", "label": "零食",
             "url": "https://www.walmart.com/browse/food/976759_976787?sort=best_seller"},
            {"key": "grocery_beverages", "label": "饮料",
             "url": "https://www.walmart.com/search?q=beverages&sort=best_seller"},
            {"key": "grocery_coffee", "label": "咖啡",
             "url": "https://www.walmart.com/search?q=coffee&sort=best_seller"},
        ],
    },
]

# Flattened key → (label, url) lookup for fast fetch
_CAT_LOOKUP: dict[str, tuple[str, str]] = {}
for _g in CATEGORY_GROUPS:
    for _it in _g["items"]:
        _CAT_LOOKUP[_it["key"]] = (_it["label"], _it["url"])

CRAWLBASE_API = "https://api.crawlbase.com/"


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class CrawlbaseWalmartService:
    """Fetch Walmart bestseller data through the Crawlbase Crawling API."""

    def __init__(
        self,
        token: Optional[str] = None,
        timeout: int = 90,
        page_delay: float = 2.0,
        max_retries: int = 2,
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._page_delay = page_delay
        self._max_retries = max_retries
        self._session = self._build_session(max_retries)

    @property
    def token(self) -> str:
        if self._token:
            return self._token
        try:
            from flask import current_app
            from services.api_key_rotator import resolve_keys

            # Env var
            env_token = current_app.config.get("CRAWLBASE_TOKEN", "") if current_app else ""
            if env_token:
                keys = resolve_keys(env_token)
                if keys:
                    return keys[0]

            # Database
            from models import get_global_config
            db_token = get_global_config().get("crawlbase_api_key", "")
            if db_token:
                keys = resolve_keys(db_token)
                if keys:
                    return keys[0]
            return ""
        except RuntimeError:
            return ""

    @staticmethod
    def _build_session(max_retries: int) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=2, pool_maxsize=4)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @staticmethod
    def list_categories() -> list[dict]:
        """Return grouped Walmart categories for optgroup dropdowns."""
        return CATEGORY_GROUPS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_category_bestsellers(
        self, category_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch bestseller products for *category_key* and return a
        cleaned list of standardised dicts.

        Walmart shows ~9-10 products per page.  This method paginates
        automatically until *limit* is reached or no more products are
        returned.

        Raises
        ------
        WalmartServiceError
            For any Crawlbase or parsing failure.
        """
        if category_key not in _CAT_LOOKUP:
            raise WalmartServiceError(
                f"Unknown category '{category_key}'. "
                f"Valid keys: {', '.join(_CAT_LOOKUP)}"
            )

        category_label, base_url = _CAT_LOOKUP[category_key]

        all_products: list[dict[str, Any]] = []
        page = 1
        # 0 = fetch all (until exhaustion, up to ~30 pages / ~300 products)
        fetch_all = limit <= 0
        max_pages = 30 if fetch_all else min((limit // 10) + 2, 12)
        effective_limit = 9999 if fetch_all else limit

        while len(all_products) < effective_limit and page <= max_pages:
            if page == 1:
                target_url = base_url
            else:
                target_url = f"{base_url}&page={page}"

            # Stagger requests to avoid connection resets / rate limits
            if page > 1 and self._page_delay > 0:
                time.sleep(self._page_delay)

            raw = self._call_crawlbase(target_url)
            products = self._parse_response(raw, category_label, limit)
            if not products:
                break  # no more products on this page

            # Deduplicate by product_name in case pages overlap
            seen_names = {p["product_name"] for p in all_products}
            for p in products:
                if p["product_name"] not in seen_names:
                    all_products.append(p)
                    seen_names.add(p["product_name"])

            if len(products) < 5:
                break  # sparse page = end of results

            page += 1

        # Assign final sequential ranks
        for i, p in enumerate(all_products):
            p["rank"] = i + 1

        return all_products[:limit]

    # ------------------------------------------------------------------
    # Product detail enrichment
    # ------------------------------------------------------------------

    def fetch_product_detail(self, product_url: str) -> dict[str, Any]:
        """Fetch enriched product detail from a Walmart product page.

        Returns a dict with: title, price, currency, brand, item_id (SKU),
        ratings, reviews_count, description, images (list), features (list),
        breadcrumbs (list), thumbnail, source_url.
        """
        raw = self._call_crawlbase(product_url)
        body = raw if isinstance(raw, dict) else {}

        return {
            "title": body.get("title", ""),
            "price": str(body.get("price", "")),
            "currency": body.get("currency", "USD"),
            "brand": body.get("brand", ""),
            "item_id": body.get("itemId", ""),
            "ratings": str(body.get("ratings", "")),
            "reviews_count": CrawlbaseWalmartService._safe_int(
                body.get("reviewsCount", 0)
            ),
            "description": body.get("description", ""),
            "images": body.get("images") if isinstance(body.get("images"), list) else [],
            "features": body.get("features") if isinstance(body.get("features"), list) else [],
            "breadcrumbs": body.get("breadCrumbs") if isinstance(body.get("breadCrumbs"), list) else [],
            "thumbnail": body.get("thumbnail", ""),
            "source_url": product_url,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_crawlbase(self, target_url: str) -> dict[str, Any]:
        """Encode *target_url*, call Crawlbase, and return the JSON body.

        Uses a shared session with retry logic for transient network errors.
        """
        token = self.token
        if not token:
            raise CrawlbaseAuthError("Crawlbase token is not configured")

        encoded_url = quote(target_url, safe="")
        api_url = (
            f"{CRAWLBASE_API}"
            f"?token={token}"
            f"&url={encoded_url}"
            f"&autoparse=true"
        )

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 2):  # session retries + manual retries
            try:
                resp = self._session.get(api_url, timeout=self._timeout)
                break  # got a response
            except requests.exceptions.Timeout:
                raise CrawlbaseTimeoutError(
                    f"Request to Crawlbase timed out after {self._timeout}s"
                ) from None
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < self._max_retries + 1:
                    wait = (attempt + 1) * 3
                    logger.warning(
                        "Crawlbase connection error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, self._max_retries + 2, wait, exc,
                    )
                    time.sleep(wait)
                continue
            except requests.exceptions.RequestException as exc:
                raise WalmartServiceError(
                    f"Crawlbase request failed: {exc}"
                ) from exc
        else:
            raise WalmartServiceError(
                f"Crawlbase request failed after {self._max_retries + 2} attempts: {last_exc}"
            ) from last_exc

        # --- Status-code classification ---
        if resp.status_code == 403:
            raise CrawlbaseAuthError("Crawlbase token invalid or quota exhausted (403)")
        if resp.status_code == 429:
            raise CrawlbaseRateLimitError(
                "Crawlbase rate limit reached (429). Retry later."
            )
        if resp.status_code == 422:
            raise CrawlbaseParseError(
                "Crawlbase could not parse the target page (422)"
            )

        if not resp.ok:
            raise WalmartServiceError(
                f"Crawlbase returned HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise WalmartServiceError(
                "Failed to parse Crawlbase response as JSON"
            ) from exc

        if not isinstance(data, dict):
            raise CrawlbaseParseError("Crawlbase returned unexpected response format")

        # --- Check body-level error statuses (Crawlbase may return 200 with errors) ---
        pc_status = data.get("pc_status")
        if pc_status and pc_status >= 400:
            if pc_status == 403:
                raise CrawlbaseAuthError(
                    f"Crawlbase returned pc_status=403 — token may not have access "
                    f"to this target or autoparse is not enabled for your plan"
                )
            if pc_status == 429:
                raise CrawlbaseRateLimitError("Crawlbase rate limit reached (pc_status=429)")
            raise WalmartServiceError(
                f"Crawlbase pc_status={pc_status}: {str(data.get('body', ''))[:200]}"
            )

        original_status = data.get("original_status")
        if original_status and original_status >= 400:
            logger.warning(
                "Target page returned HTTP %s — page may be blocked or unavailable",
                original_status,
            )

        # Crawlbase wraps the parsed page inside a "body" key
        body = data.get("body") if isinstance(data, dict) else {}
        if not body:
            body = data

        if isinstance(body, dict):
            return body
        raise CrawlbaseParseError(
            "Crawlbase returned unstructured data — "
            "the target URL may have changed or autoparse failed"
        )

    def _parse_response(
        self, body: dict[str, Any], category_label: str, limit: int
    ) -> list[dict[str, Any]]:
        """Walk the Crawlbase autoparse body and normalise product entries."""

        # --- Try to locate the product list ---
        products_raw: list[dict[str, Any]] = []

        # Common Crawlbase autoparse nesting for e-commerce pages
        for candidate_key in (
            "products",
            "items",
            "results",
            "productList",
            "listings",
        ):
            candidate = body.get(candidate_key)
            if isinstance(candidate, list) and candidate:
                products_raw = candidate
                break

        # Fallback: iterate top-level keys and pick the first list of dicts
        if not products_raw:
            for val in body.values():
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    products_raw = val
                    break

        if not products_raw:
            raise CrawlbaseParseError(
                "Could not locate product list in Crawlbase response — "
                "the Walmart page structure may have changed"
            )

        # --- Normalise each product ---
        cleaned: list[dict[str, Any]] = []
        for idx, item in enumerate(products_raw):
            if limit > 0 and idx >= limit:
                break
            mapped = self._normalise_product(item, category_label, idx + 1)
            if mapped.get("product_name"):  # require at least a name
                cleaned.append(mapped)

        return cleaned

    # ------------------------------------------------------------------
    # Field normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_product(
        item: dict[str, Any], category_label: str, rank: int
    ) -> dict[str, Any]:
        """Map a raw Crawlbase Walmart product dict to the standard schema.

        Crawlbase returns fields: position, title, price{currentPrice,rawPrice},
        ratings (string), reviewsCount (string), link, image, outOfStock, isBestSeller.
        """

        # --- Price extraction ---
        price_obj = item.get("price")
        if isinstance(price_obj, dict):
            raw_price = price_obj.get("rawPrice") or price_obj.get("currentPrice") or ""
        else:
            raw_price = price_obj or ""
        price = CrawlbaseWalmartService._clean_price(raw_price)

        # --- Rating (Crawlbase uses "ratings" — empty string when none) ---
        raw_rating = item.get("ratings") or 0
        rating_score = CrawlbaseWalmartService._safe_float(raw_rating)

        # --- Review count (Crawlbase returns string like "2883") ---
        raw_reviews = item.get("reviewsCount") or 0
        review_count = CrawlbaseWalmartService._safe_int(raw_reviews)

        # --- Product name (Crawlbase uses "title") ---
        product_name = item.get("title") or item.get("name") or item.get("productName") or ""

        # --- Source URL (Crawlbase uses "link") ---
        source_url = item.get("link") or item.get("url") or ""
        # Crawlbase leaks the sort query param into relative product paths:
        #   https://www.walmart.com?sort=best_seller/ip/... → /ip/...
        source_url = source_url.replace("?sort=best_seller/", "/")
        if source_url and not source_url.startswith("http"):
            source_url = f"https://www.walmart.com{source_url}"

        # --- Identity code (not provided by Crawlbase for Walmart) ---
        identity_code = (
            item.get("upc")
            or item.get("gtin")
            or item.get("asin")
            or item.get("sku")
            or item.get("productId")
            or ""
        )

        return {
            "category": category_label,
            "rank": rank,
            "product_name": str(product_name).strip(),
            "price": price,
            "identity_code": str(identity_code).strip(),
            "rating_score": rating_score,
            "review_count": review_count,
            "source_url": str(source_url).strip(),
        }

    # ------------------------------------------------------------------
    # Type coercion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _clean_price(raw: Any) -> float:
        """Strip currency symbols and convert to float."""
        if raw is None:
            return 0.0
        s = str(raw).strip()
        if not s:
            return 0.0
        # Remove common currency symbols / codes
        for char in ("$", "€", "£", "¥", "USD", "EUR", "GBP", "CNY", ","):
            s = s.replace(char, "")
        try:
            return round(float(s), 2)
        except (TypeError, ValueError):
            return 0.0
