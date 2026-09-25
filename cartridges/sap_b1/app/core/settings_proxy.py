"""Proxy para leer system_settings desde el console. Fallback a env si falla."""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 30
_CONSOLE_URL = os.environ.get("CONSOLE_URL", "http://console:8000")
_INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY_SAP_B1_TO_CONSOLE", "")
if not _INTERNAL_KEY and os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}:
    _INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "")
if not _INTERNAL_KEY and os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}:
    _INTERNAL_KEY = os.environ.get("INTERNAL_API_KEY", "")


def _fetch_from_console(key: str) -> str | None:
    if not _INTERNAL_KEY:
        return None
    try:
        headers = {"x-api-key": _INTERNAL_KEY, "x-internal-service": "cartridge-sap_b1"}
        with httpx.Client(timeout=2.0) as c:
            r = c.get(f"{_CONSOLE_URL}/internal/settings/{key}/reveal", headers=headers)
            if r.status_code == 200:
                body = r.json()
                v = body.get("value")
                if isinstance(v, str):
                    return v
                if v is None:
                    return ""
                return str(v)
    except Exception as e:
        logger.debug("settings_proxy fetch failed for %s: %s", key, e)
    return None


def get_setting(key: str, default: str = "", env_fallback: str | None = None) -> str:
    """Lee setting desde console (cacheado 30s). Fallback a env si console falla."""
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    value = _fetch_from_console(key)
    if value is None and env_fallback:
        value = os.environ.get(env_fallback, default)
    elif value is None:
        value = default

    _CACHE[key] = (now, value)
    return value
