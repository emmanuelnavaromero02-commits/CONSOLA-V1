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

logger = logging.getLogger(__name__)

class _ConnectionCache:
    def __init__(
        self,
        ttl_seconds: float = 300.0,
        max_entries: int = 256,
        max_stale_seconds: float = 12 * 3600.0,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._max_stale = max_stale_seconds
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def _prune(self, now: float) -> None:
        for key in [k for k, (stored_at, _) in self._items.items() if now - stored_at > self._max_stale]:
            self._items.pop(key, None)

    def get(self, key: Any, default: Any = None) -> Any:
        now = time.monotonic()
        self._prune(now)
        item = self._items.get(key)
        if item is None:
            return default
        stored_at, value = item
        if now - stored_at >= self._ttl:
            return default
        self._items.move_to_end(key)
        return value

    def stale(self, key: Any, default: Any = None) -> Any:
        self._prune(time.monotonic())
        item = self._items.get(key)
        return default if item is None else item[1]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._items[key] = (time.monotonic(), value)
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
    "SAP_B1_DIALECT": ("dialect", "sap_b1_dialect"),
    "SAP_B1_HOST": ("host", "hostname", "address", "server", "sap_b1_host"),
    "SAP_B1_PORT": ("port", "sap_b1_port"),
    "SAP_B1_USER": ("user", "username", "sap_b1_user"),
    "SAP_B1_PASSWORD": ("password", "pass", "sap_b1_password"),
    "SAP_B1_DATABASE": ("database", "database_name", "tenant_database", "sap_b1_database"),
    "SAP_B1_COMPANIES": ("companies", "company_schemas", "sap_b1_companies"),
    "SAP_B1_INTERCOMPANY": ("intercompany", "intercompany_partners", "sap_b1_intercompany"),
    "SAP_B1_BUSINESS_PARAMETERS": ("business_parameters", "sap_b1_business_parameters"),
    "SAP_B1_ENCRYPT": ("encrypt", "tls", "sap_b1_encrypt"),
    "SAP_B1_SSL_VALIDATE_CERTIFICATE": ("ssl_validate_certificate", "sap_b1_ssl_validate_certificate"),
}

_SERVICE_HEADERS: dict[str, tuple[str, ...]] = {
    "replicon": ("replicon", "cartridge-replicon"),
    "hubspot": ("hubspot", "cartridge-hubspot"),
    "sap_hcm": ("cartridge-sap_hcm",),
    "sap_s4hana": ("cartridge-sap_s4hana",),
    "sap_successfactors": ("cartridge-sap_successfactors",),
    "sap_b1": ("cartridge-sap_b1",),
}

_SERVICE_KEY_ENVS: dict[str, str] = {
    "replicon": "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
    "hubspot": "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
    "sap_hcm": "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
    "sap_s4hana": "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
    "sap_successfactors": "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
    "sap_b1": "INTERNAL_API_KEY_SAP_B1_TO_CONSOLE",
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


def _flatten_connection_fields(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("fields")
    if not isinstance(nested, dict):
        return payload
    flattened = dict(payload)
    for key, value in nested.items():
        flattened.setdefault(str(key), value)
    return flattened


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
                    payload = _flatten_connection_fields(payload)
                    _CONNECTION_CACHE[cache_key] = payload
                    return payload
            except Exception as exc:
                logger.debug("Vault reveal failed for %s/%s: %s", service, conn_id, type(exc).__name__)
    return _CONNECTION_CACHE.stale(cache_key, {})


def _candidate_fields(env_var_name: str) -> tuple[str, ...]:
    direct = env_var_name.strip()
    fields: list[str] = []
    fields.extend(_FIELD_ALIASES.get(direct, ()))
    fields.extend((direct, direct.lower()))
    for prefix in ("SAP_B1_",):
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


def get_sap_b1_credentials() -> dict[str, str]:
    resolved = {
        "dialect": get_secret_for_worker("sap_b1", "SAP_B1_DIALECT") or settings.sap_b1_dialect,
        "host": get_secret_for_worker("sap_b1", "SAP_B1_HOST") or settings.sap_b1_host,
        "port": get_secret_for_worker("sap_b1", "SAP_B1_PORT") or settings.sap_b1_port,
        "user": get_secret_for_worker("sap_b1", "SAP_B1_USER") or settings.sap_b1_user,
        "password": get_secret_for_worker("sap_b1", "SAP_B1_PASSWORD") or settings.sap_b1_password,
        "database": get_secret_for_worker("sap_b1", "SAP_B1_DATABASE") or settings.sap_b1_database,
        "companies": get_secret_for_worker("sap_b1", "SAP_B1_COMPANIES") or settings.sap_b1_companies,
    }
    if not all([resolved["host"], resolved["user"], resolved["password"]]):
        raise ValueError(
            "SAP Business One credentials not configured.\n"
            "Set SAP_B1_* variables or save connections/sap_b1/default in Vault."
        )
    return resolved


def get_secret(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
