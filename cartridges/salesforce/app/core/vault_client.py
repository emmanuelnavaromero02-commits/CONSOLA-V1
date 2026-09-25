from __future__ import annotations

import logging
import os
import time
import hashlib
import json
from collections import OrderedDict
from typing import Any

import requests

from app.core.config import settings
from app.core.settings_proxy import get_setting

logger = logging.getLogger(__name__)

class _ConnectionCache:
    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 256) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        item = self._items.get(key)
        if item is None:
            return default
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._items.pop(key, None)
            return default
        self._items.move_to_end(key)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        self._items[key] = (time.monotonic() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)

    def pop(self, key: Any, default: Any = None) -> Any:
        item = self._items.pop(key, None)
        return default if item is None else item[1]

    def clear(self) -> None:
        self._items.clear()


_MISSING = object()

_CONNECTION_CACHE = _ConnectionCache()

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "SF_BASE_URL": ("base_url", "url", "host", "instance_url", "sf_base_url"),
    "SF_COMPANY_ID": ("company_id", "company", "sf_company_id"),
    "SF_CLIENT_ID": ("client_id", "consumer_key", "sf_client_id"),
    "SF_CLIENT_SECRET": ("client_secret", "secret", "consumer_secret", "sf_client_secret"),
    "SF_USERNAME": ("username", "user", "sf_username"),
    "SF_PASSWORD": ("password", "pass", "sf_password"),
    "SF_SECURITY_TOKEN": ("security_token", "sf_security_token"),
    "SF_TOKEN_URL": ("token_url", "oauth_token_url", "sf_token_url"),
    "SF_ACCESS_TOKEN": ("access_token", "token", "api_token", "sf_access_token"),
    "SF_API_KEY": ("api_key", "token", "api_token", "sf_api_key"),
}

_SERVICE_HEADERS: dict[str, tuple[str, ...]] = {
    "replicon": ("replicon", "cartridge-replicon"),
    "sap_hcm": ("cartridge-sap_hcm",),
    "sap_s4hana": ("cartridge-sap_s4hana",),
    "salesforce": ("cartridge-salesforce",),
}

_SERVICE_KEY_ENVS: dict[str, str] = {
    "replicon": "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
    "salesforce": "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _auth_options(service_name: str) -> list[tuple[str, str]]:
    service = service_name.strip().lower()
    options: list[tuple[str, str]] = []
    key_env = _SERVICE_KEY_ENVS.get(service)
    if key_env:
        key = os.environ.get(key_env, "")
        if key:
            for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
                options.append((key, header))
    if not options and not _is_production():
        key = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "")
        if key:
            for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
                options.append((key, header))
    if not options and not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
                options.append((legacy, header))
    return options


def _context_cache_key(security_context: str | None) -> str:
    value = (security_context or "").strip()
    if not value:
        return ""
    tenant_id = ""
    workspace_id = ""
    try:
        ctx = json.loads(value)
    except ValueError:
        ctx = None
    if isinstance(ctx, dict):
        tenant_id = str(ctx.get("tenant_id") or "").strip()
        workspace_id = str(ctx.get("workspace_id") or "").strip()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"tenant_id={tenant_id}/workspace_id={workspace_id}/{digest}"


def _fetch_connection(service_name: str, security_context: str | None = None) -> dict[str, Any]:
    service = service_name.strip().lower()
    scoped_context = (security_context or "").strip() or None
    cache_key = (service, _context_cache_key(scoped_context))
    cached = _CONNECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    console_url = os.environ.get("CONSOLE_URL", "http://console:8000").rstrip("/")
    for conn_id in ("default", "analytics"):
        for key, internal_service in _auth_options(service):
            try:
                headers = {"x-api-key": key, "x-internal-service": internal_service}
                if scoped_context:
                    headers["x-security-context"] = scoped_context
                response = requests.get(
                    f"{console_url}/api/vault/connections/{service}/{conn_id}/reveal",
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
                    _CONNECTION_CACHE[cache_key] = payload
                    return payload
            except Exception as exc:
                logger.debug("Vault reveal failed for %s/%s: %s", service, conn_id, exc)
    return {}


def _candidate_fields(env_var_name: str) -> tuple[str, ...]:
    direct = env_var_name.strip()
    fields: list[str] = []
    fields.extend(_FIELD_ALIASES.get(direct, ()))
    fields.extend((direct, direct.lower()))
    for prefix in ("REPLICON_", "SAP_HCM_", "SAP_S4_", "SF_"):
        if direct.startswith(prefix):
            fields.append(direct.removeprefix(prefix).lower())
    return tuple(dict.fromkeys(fields))


def get_secret_for_worker(service_name: str, env_var_name: str, security_context: str | None = None) -> str:
    value = os.environ.get(env_var_name)
    if value:
        return value

    payload = _fetch_connection(service_name, security_context=security_context)
    for field in _candidate_fields(env_var_name):
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def get_connection_for_worker(service_name: str, security_context: str | None = None) -> dict[str, Any]:
    return dict(_fetch_connection(service_name, security_context=security_context))


def get_salesforce_credentials() -> tuple[str, str, str, str, str]:
    base_url = (
        get_secret_for_worker("salesforce", "SF_BASE_URL")
        or get_setting("salesforce_base_url", default=settings.sf_base_url, env_fallback="SF_BASE_URL")
    )
    company_id = get_secret_for_worker("salesforce", "SF_COMPANY_ID") or settings.sf_company_id
    client_id = (
        get_secret_for_worker("salesforce", "SF_CLIENT_ID")
        or get_setting("salesforce_client_id", default=settings.sf_client_id, env_fallback="SF_CLIENT_ID")
    )
    client_secret = (
        get_secret_for_worker("salesforce", "SF_CLIENT_SECRET")
        or get_setting(
            "salesforce_client_secret",
            default=settings.sf_client_secret,
            env_fallback="SF_CLIENT_SECRET",
        )
    )
    token_url = get_secret_for_worker("salesforce", "SF_TOKEN_URL") or settings.sf_token_url

    if not all([base_url, client_id, client_secret, token_url]):
        raise ValueError(
            "Salesforce credentials not configured.\n"
            "Set SF_* variables or save connections/salesforce/default in Vault."
        )
    return base_url, company_id, client_id, client_secret, token_url


def get_secret(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
