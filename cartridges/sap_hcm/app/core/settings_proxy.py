"""Proxy para leer system_settings desde el console. Fallback a env si falla.

Uso:
    from app.core.settings_proxy import get_setting
    token = get_setting("sap_hcm_token", default="", env_fallback="SAP_HCM_TOKEN")
"""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 30
_CONSOLE_URL = os.environ.get("CONSOLE_URL", "http://console:8000")
# Sprint v1.12: dedicated cartridge→console key, with legacy fallback.
_INTERNAL_KEY = (
    os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE")
    or os.environ.get("INTERNAL_API_KEY", "")
)


def _fetch_from_console(key: str) -> str | None:
    if not _INTERNAL_KEY:
        return None
    try:
        # Sprint v1.12: console now whitelists "cartridge-<name>" with its
        # own pair key. Send the cartridge-specific service identifier
        # instead of impersonating airflow.
        headers = {"x-api-key": _INTERNAL_KEY, "x-internal-service": "cartridge-sap_hcm"}
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
    """Lee setting desde console (cacheado 30s). Fallback a env si console falla.

    Args:
        key: nombre del setting en system_settings (ej. 'sap_hcm_token').
        default: valor si nada está configurado.
        env_fallback: nombre de variable de entorno a usar como fallback.
    """
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
