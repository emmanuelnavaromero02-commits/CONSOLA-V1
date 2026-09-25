from __future__ import annotations

import logging
import os
import threading
import time
import re
import hashlib
from collections import OrderedDict
from typing import Any

import requests

from app.core.config import settings
from app.core.settings_proxy import get_setting

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
        self._lock = threading.RLock()

    def _prune(self, now: float) -> None:
        for key in [k for k, (stored_at, _) in list(self._items.items()) if now - stored_at > self._max_stale]:
            self._items.pop(key, None)

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
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
        with self._lock:
            self._prune(time.monotonic())
            item = self._items.get(key)
            return default if item is None else item[1]

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            self._items[key] = (time.monotonic(), value)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __iter__(self):
        with self._lock:
            return iter(list(self._items))

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def pop(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            item = self._items.pop(key, None)
            return default if item is None else item[1]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_MISSING = object()

_CONNECTION_CACHE = _ConnectionCache()
_DEFAULT_CONN_IDS = ("default", "analytics")
_CONN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "SF_BASE_URL": ("base_url", "url", "host", "sf_base_url"),
    "SF_COMPANY_ID": ("company_id", "company", "sf_company_id"),
    "SF_CLIENT_ID": ("client_id", "sf_client_id"),
    "SF_CLIENT_SECRET": ("client_secret", "secret", "password", "sf_client_secret"),
    "SF_TOKEN_URL": ("token_url", "oauth_token_url", "sf_token_url"),
    "SF_IDP_URL": ("idp_url", "oauth_idp_url", "sf_idp_url"),
    "SF_AUTH_METHOD": ("auth_method", "sf_auth_method"),
    "SF_ADMIN_USER": ("admin_user", "user_id", "sf_admin_user"),
    "SF_PRIVATE_KEY_PATH": ("private_key_path", "sf_private_key_path"),
    "SF_PRIVATE_KEY_PEM": ("private_key_pem", "private_key", "sf_private_key_pem"),
    "SF_ACCESS_TOKEN": ("access_token", "token", "api_token", "sf_access_token"),
    "SF_API_KEY": ("api_key", "token", "api_token", "sf_api_key"),
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
    if service == "sap_successfactors":
        airflow_key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", "")
        if airflow_key:
            options.append((airflow_key, "airflow"))
    if not options and not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
                options.append((legacy, header))
    return options


def _connection_ids(conn_id: str | None = None) -> tuple[str, ...]:
    requested = (conn_id or "").strip()
    if not requested:
        return _DEFAULT_CONN_IDS
    if not _CONN_ID_RE.fullmatch(requested):
        raise ValueError("invalid Vault connection id")
    return (requested,)


def _context_cache_key(security_context: str | None) -> str:
    value = (security_context or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fetch_connection(
    service_name: str,
    conn_id: str | None = None,
    security_context: str | None = None,
) -> dict[str, Any]:
    service = service_name.strip().lower()
    scoped_context = (security_context or "").strip() or None
    cache_key = (service, (conn_id or "").strip(), _context_cache_key(scoped_context))
    cached = _CONNECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    console_url = os.environ.get("CONSOLE_URL", "http://console:8000").rstrip("/")
    for candidate_conn_id in _connection_ids(conn_id):
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
                    continue
                if response.status_code in {401, 403}:
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    payload = _flatten_connection_fields(payload)
                    payload.setdefault("conn_id", candidate_conn_id)
                    payload.setdefault("id", candidate_conn_id)
                    _CONNECTION_CACHE[cache_key] = payload
                    return payload
            except Exception as exc:
                logger.debug("Vault reveal failed for %s/%s: %s", service, candidate_conn_id, exc)
    return _CONNECTION_CACHE.stale(cache_key, {})


def _flatten_connection_fields(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("fields")
    if not isinstance(nested, dict):
        return payload
    flattened = dict(payload)
    for key, value in nested.items():
        flattened.setdefault(str(key), value)
    return flattened


def _candidate_fields(env_var_name: str) -> tuple[str, ...]:
    direct = env_var_name.strip()
    fields: list[str] = []
    fields.extend(_FIELD_ALIASES.get(direct, ()))
    fields.extend((direct, direct.lower()))
    for prefix in ("REPLICON_", "SAP_HCM_", "SAP_S4_", "SF_"):
        if direct.startswith(prefix):
            fields.append(direct.removeprefix(prefix).lower())
    return tuple(dict.fromkeys(fields))


def get_secret_for_worker(
    service_name: str,
    env_var_name: str,
    conn_id: str | None = None,
    security_context: str | None = None,
) -> str:
    value = os.environ.get(env_var_name)
    if value:
        return value

    payload = _fetch_connection(service_name, conn_id=conn_id, security_context=security_context)
    for field in _candidate_fields(env_var_name):
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def get_connection_for_worker(
    service_name: str,
    conn_id: str | None = None,
    security_context: str | None = None,
) -> dict[str, Any]:
    return dict(_fetch_connection(service_name, conn_id=conn_id, security_context=security_context))


def get_sap_successfactors_credentials() -> tuple[str, str, str, str, str]:
    base_url = (
        get_secret_for_worker("sap_successfactors", "SF_BASE_URL")
        or get_setting("sap_successfactors_base_url", default=settings.sf_base_url, env_fallback="SF_BASE_URL")
    )
    company_id = get_secret_for_worker("sap_successfactors", "SF_COMPANY_ID") or settings.sf_company_id
    client_id = (
        get_secret_for_worker("sap_successfactors", "SF_CLIENT_ID")
        or get_setting("sap_successfactors_client_id", default=settings.sf_client_id, env_fallback="SF_CLIENT_ID")
    )
    client_secret = (
        get_secret_for_worker("sap_successfactors", "SF_CLIENT_SECRET")
        or get_setting(
            "sap_successfactors_client_secret",
            default=settings.sf_client_secret,
            env_fallback="SF_CLIENT_SECRET",
        )
    )
    token_url = get_secret_for_worker("sap_successfactors", "SF_TOKEN_URL") or settings.sf_token_url

    if not all([company_id, client_id, client_secret, token_url]):
        raise ValueError(
            "SAP SuccessFactors credentials not configured.\n"
            "Set SF_* variables or save connections/sap_successfactors/default in Vault."
        )
    return base_url, company_id, client_id, client_secret, token_url


def get_secret(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
