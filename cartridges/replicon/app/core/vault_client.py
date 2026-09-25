from __future__ import annotations

import logging
import os
import time
import hashlib
from collections import OrderedDict
from typing import Any

import requests

from app.core.config import settings

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

_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "REPLICON_API_TOKEN": ("REPLICON_API_TOKEN", "REPLICON_TOKEN", "REPLICON_API_KEY"),
    "REPLICON_TOKEN": ("REPLICON_TOKEN", "REPLICON_API_TOKEN", "REPLICON_API_KEY"),
    "REPLICON_API_KEY": ("REPLICON_API_KEY", "REPLICON_API_TOKEN", "REPLICON_TOKEN"),
}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "REPLICON_BASE_URL": ("base_url", "url", "host", "replicon_base_url"),
    "REPLICON_API_TOKEN": ("token", "api_token", "api_key", "password", "replicon_api_token"),
    "REPLICON_TOKEN": ("token", "api_token", "api_key", "password", "replicon_token"),
    "REPLICON_API_KEY": ("api_key", "token", "api_token", "password", "replicon_api_key"),
    "REPLICON_USER": ("user", "username", "replicon_user"),
    "REPLICON_PASSWORD": ("password", "pass", "token", "replicon_password"),
}

_SERVICE_HEADERS: dict[str, tuple[str, ...]] = {
    "replicon": ("replicon", "cartridge-replicon"),
    "hubspot": ("hubspot", "cartridge-hubspot"),
    "sap_hcm": ("cartridge-sap_hcm",),
    "sap_s4hana": ("cartridge-sap_s4hana",),
    "sap_successfactors": ("cartridge-sap_successfactors",),
}

_SERVICE_KEY_ENVS: dict[str, str] = {
    "replicon": "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
    "hubspot": "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
    "sap_hcm": "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
    "sap_s4hana": "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
    "sap_successfactors": "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _auth_options(service_name: str) -> list[tuple[str, str]]:
    service = service_name.strip().lower()
    options: list[tuple[str, str]] = []
    key_env = _SERVICE_KEY_ENVS.get(service)
    key = os.environ.get(key_env or "", "") if key_env else ""
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
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_conn_id(conn_id: str | None) -> str:
    return (conn_id or "").strip()


def _connection_candidates(conn_id: str | None) -> tuple[str, ...]:
    selected = _normalize_conn_id(conn_id)
    if selected:
        return (selected,)
    return ("default", "analytics")


def _fetch_connection(
    service_name: str,
    security_context: str | None = None,
    conn_id: str | None = None,
) -> dict[str, Any]:
    service = service_name.strip().lower()
    scoped_context = (security_context or "").strip() or None
    selected_conn_id = _normalize_conn_id(conn_id)
    cache_key = (service, _context_cache_key(scoped_context), selected_conn_id)
    cached = _CONNECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    console_url = os.environ.get("CONSOLE_URL", "http://console:8000").rstrip("/")
    for candidate_conn_id in _connection_candidates(conn_id):
        for key, internal_service in _auth_options(service):
            try:
                headers = {"x-api-key": key, "x-internal-service": internal_service}
                if scoped_context:
                    headers["x-security-context"] = scoped_context
                response = requests.get(
                    f"{console_url}/api/vault/connections/{service}/{candidate_conn_id}/reveal",
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
                logger.debug("Vault reveal failed for %s/%s: %s", service, candidate_conn_id, exc)
    return {}


def _candidate_fields(env_var_name: str) -> tuple[str, ...]:
    direct = env_var_name.strip()
    lower = direct.lower()
    fields: list[str] = []
    fields.extend(_FIELD_ALIASES.get(direct, ()))
    fields.extend((direct, lower))
    for prefix in ("REPLICON_", "SAP_HCM_", "SAP_S4_", "SF_"):
        if direct.startswith(prefix):
            fields.append(direct.removeprefix(prefix).lower())
    return tuple(dict.fromkeys(fields))


def get_secret_for_worker(
    service_name: str,
    env_var_name: str,
    security_context: str | None = None,
    conn_id: str | None = None,
) -> str:
    for candidate in _ENV_ALIASES.get(env_var_name, (env_var_name,)):
        value = os.environ.get(candidate)
        if value:
            return value

    payload = _fetch_connection(service_name, security_context=security_context, conn_id=conn_id)
    for field in _candidate_fields(env_var_name):
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def get_connection_for_worker(
    service_name: str,
    security_context: str | None = None,
    conn_id: str | None = None,
) -> dict[str, Any]:
    return dict(_fetch_connection(service_name, security_context=security_context, conn_id=conn_id))


def get_replicon_connection(
    security_context: str | None = None,
    conn_id: str | None = None,
) -> dict[str, Any]:
    payload = get_connection_for_worker("replicon", security_context=security_context, conn_id=conn_id)
    connection = dict(payload)
    connection["base_url"] = (
        get_secret_for_worker(
            "replicon",
            "REPLICON_BASE_URL",
            security_context=security_context,
            conn_id=conn_id,
        )
        or settings.replicon_base_url
    )
    auth_method = str(connection.get("auth_method") or "bearer_token").strip().lower()
    if auth_method == "basic":
        user = get_secret_for_worker("replicon", "REPLICON_USER", security_context=security_context, conn_id=conn_id)
        password = get_secret_for_worker("replicon", "REPLICON_PASSWORD", security_context=security_context, conn_id=conn_id)
        if user:
            connection["user"] = user
        if password:
            connection["password"] = password
    else:
        token = (
            get_secret_for_worker(
                "replicon",
                "REPLICON_API_TOKEN",
                security_context=security_context,
                conn_id=conn_id,
            )
            or settings.replicon_api_token
            or ""
        )
        if token:
            if auth_method in {"api_key", "apikey", "x_api_key"}:
                connection.setdefault("api_key", token)
            else:
                connection.setdefault("token", token)
    connection.setdefault("auth_method", auth_method)
    return connection


def get_replicon_credentials() -> tuple[str, str]:
    connection = get_replicon_connection()
    base_url = str(connection.get("base_url") or "")
    token = (
        str(connection.get("token") or "")
        or str(connection.get("api_token") or "")
        or str(connection.get("api_key") or "")
        or str(connection.get("password") or "")
    )
    if not token:
        raise ValueError(
            "Replicon API token not configured.\n"
            "Set REPLICON_API_TOKEN or save connections/replicon/default in Vault."
        )
    return base_url, token
