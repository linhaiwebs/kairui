"""API Key rotation — try multiple keys sequentially on failure."""

import json
import logging
import requests as http_requests

logger = logging.getLogger(__name__)

# HTTP statuses that indicate the key is exhausted/invalid → worth trying next key
_ROTATABLE_STATUSES = frozenset({401, 402, 403, 429, 500, 502, 503, 504})
_ROTATABLE_EXCEPTIONS = (
    http_requests.exceptions.Timeout,
    http_requests.exceptions.ConnectionError,
)


def resolve_keys(raw_value):
    """Parse stored value into a list of key strings.

    Compatible with both old (plain string) and new (JSON array) formats.
    Empty strings / whitespace-only entries are filtered out.
    """
    if not raw_value:
        return []
    raw_value = raw_value.strip()
    if raw_value.startswith("["):
        try:
            keys = json.loads(raw_value)
            if isinstance(keys, list):
                return [k.strip() for k in keys if k and k.strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    # Legacy: single plain-string key
    return [raw_value]


def _build_keys(primary_key=None, db_key=None, env_token=None):
    """Merge key sources into a deduplicated, ordered list."""
    seen = set()
    keys = []
    for src in (primary_key, db_key, env_token):
        if src:
            for k in resolve_keys(src):
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
    return keys


def rotate_deepseek(call_fn, keys):
    """Try *call_fn(key)* for each key until one succeeds.

    *call_fn* receives a single key string and must return a response object
    (or raise).  Returns the first successful result.

    Raises the last captured exception when all keys are exhausted.
    """
    if not keys:
        raise ValueError("No DeepSeek API keys configured")
    last_error = None
    for i, key in enumerate(keys):
        try:
            result = call_fn(key)
            if i > 0:
                logger.info("DeepSeek key[%d] succeeded after failover", i)
            return result
        except _ROTATABLE_EXCEPTIONS as e:
            logger.warning("DeepSeek key[%d] network error: %s", i, e)
            last_error = e
            continue
        except Exception as e:
            # Check if it's an HTTPError with a rotatable status
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in _ROTATABLE_STATUSES:
                logger.warning("DeepSeek key[%d] HTTP %s, rotating", i, status)
                last_error = e
                continue
            raise
    logger.error("All %d DeepSeek keys exhausted", len(keys))
    raise last_error or RuntimeError("All DeepSeek keys failed")


def rotate_crawlbase(call_fn, keys):
    """Try *call_fn(key)* for each Crawlbase token until one succeeds.

    Same semantics as :func:`rotate_deepseek`.
    """
    if not keys:
        raise ValueError("No Crawlbase tokens configured")
    last_error = None
    for i, key in enumerate(keys):
        try:
            result = call_fn(key)
            if i > 0:
                logger.info("Crawlbase key[%d] succeeded after failover", i)
            return result
        except _ROTATABLE_EXCEPTIONS as e:
            logger.warning("Crawlbase key[%d] network error: %s", i, e)
            last_error = e
            continue
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in _ROTATABLE_STATUSES:
                logger.warning("Crawlbase key[%d] HTTP %s, rotating", i, status)
                last_error = e
                continue
            raise
    logger.error("All %d Crawlbase keys exhausted", len(keys))
    raise last_error or RuntimeError("All Crawlbase tokens failed")


def get_deepseek_keys(db_config=None):
    """Resolve DeepSeek keys from database config dict."""
    if db_config is None:
        from models import get_global_config
        db_config = get_global_config()
    return resolve_keys(db_config.get("deepseek_api_key", ""))


def get_crawlbase_keys(db_config=None):
    """Resolve Crawlbase tokens from database + env var."""
    from flask import current_app
    db_val = ""
    env_val = current_app.config.get("CRAWLBASE_TOKEN", "") if current_app else ""
    if db_config is None:
        from models import get_global_config
        db_config = get_global_config()
    db_val = db_config.get("crawlbase_api_key", "")
    return _build_keys(db_key=db_val, env_token=env_val)
