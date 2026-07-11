from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_FIELD_ALIASES: tuple[str, ...] = ("user_agent", "sec_user_agent", "SEC_EDGAR_USER_AGENT")
_SERVICE_HEADERS: tuple[str, ...] = ("sec_edgar", "cartridge-sec-edgar")
_CONN_ID_RE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
_LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test"}


def _runtime_env() -> str:
    return os.environ.get("APP_ENV", "production").strip().lower() or "production"


def _local_fallbacks_allowed() -> bool:
    return _runtime_env() in _LOCAL_ENVIRONMENTS


def _normalize_conn_id(conn_id: str | None) -> str:
    value = (conn_id or "").strip()
    if value and any(ch not in _CONN_ID_RE for ch in value):
        raise ValueError("invalid Vault connection id")
    return value


def _auth_options() -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    key = os.environ.get("INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE", "")
    if key:
        options.extend((key, header) for header in _SERVICE_HEADERS)
    if not options and _local_fallbacks_allowed():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            options.extend((legacy, header) for header in _SERVICE_HEADERS)
    return options


def _connection_candidates(conn_id: str | None) -> tuple[str, ...]:
    selected = _normalize_conn_id(conn_id)
    return (selected,) if selected else ("default",)


def _fetch_connection(conn_id: str | None, security_context: str | None) -> dict[str, Any]:
    scoped_context = (security_context or "").strip() or None
    console_url = os.environ.get("CONSOLE_URL", "http://console:8000").rstrip("/")
    for candidate_conn_id in _connection_candidates(conn_id):
        for key, internal_service in _auth_options():
            try:
                headers = {"x-api-key": key, "x-internal-service": internal_service}
                if scoped_context:
                    headers["x-security-context"] = scoped_context
                response = requests.get(
                    f"{console_url}/api/vault/connections/sec_edgar/{candidate_conn_id}/reveal",
                    headers=headers,
                    timeout=5,
                )
                if response.status_code == 404:
                    break
                if response.status_code in {401, 403}:
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
            except Exception as exc:
                logger.debug("SEC EDGAR Vault reveal failed for %s: %s", candidate_conn_id, type(exc).__name__)
    return {}


def resolve_user_agent(*, conn_id: str | None = None, security_context: str | None = None) -> str:
    selected_conn_id = _normalize_conn_id(conn_id)
    payload = _fetch_connection(conn_id, security_context) if selected_conn_id else {}
    for field in _FIELD_ALIASES:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return settings.require_user_agent()
