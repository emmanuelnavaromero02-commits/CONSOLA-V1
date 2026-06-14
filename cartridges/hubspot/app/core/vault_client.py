from __future__ import annotations

import logging
import os
import hashlib
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_CONNECTION_CACHE: dict[tuple[str, str], dict[str, Any]] = {}

_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "HUBSPOT_API_TOKEN": ("HUBSPOT_API_TOKEN", "HUBSPOT_TOKEN", "HUBSPOT_API_KEY"),
    "HUBSPOT_TOKEN": ("HUBSPOT_TOKEN", "HUBSPOT_API_TOKEN", "HUBSPOT_API_KEY"),
    "HUBSPOT_API_KEY": ("HUBSPOT_API_KEY", "HUBSPOT_API_TOKEN", "HUBSPOT_TOKEN"),
}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "HUBSPOT_BASE_URL": ("base_url", "url", "host", "hubspot_base_url"),
    "HUBSPOT_API_TOKEN": ("token", "api_token", "api_key", "password", "hubspot_api_token"),
    "HUBSPOT_TOKEN": ("token", "api_token", "api_key", "password", "hubspot_token"),
    "HUBSPOT_API_KEY": ("api_key", "token", "api_token", "password", "hubspot_api_key"),
    "HUBSPOT_USER": ("user", "username", "hubspot_user"),
    "HUBSPOT_PASSWORD": ("password", "pass", "token", "hubspot_password"),
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
    lower = direct.lower()
    fields: list[str] = []
    fields.extend(_FIELD_ALIASES.get(direct, ()))
    fields.extend((direct, lower))
    for prefix in ("HUBSPOT_", "SAP_HCM_", "SAP_S4_", "SF_"):
        if direct.startswith(prefix):
            fields.append(direct.removeprefix(prefix).lower())
    return tuple(dict.fromkeys(fields))


def get_secret_for_worker(service_name: str, env_var_name: str, security_context: str | None = None) -> str:
    """Resolve a worker credential from env first, then Console Vault."""
    for candidate in _ENV_ALIASES.get(env_var_name, (env_var_name,)):
        value = os.environ.get(candidate)
        if value:
            return value

    payload = _fetch_connection(service_name, security_context=security_context)
    for field in _candidate_fields(env_var_name):
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def get_secret(env_var_name: str, default: str = "") -> str:
    """Single-arg secret access used by protection_service (mirrors the SAP
    cartridge signature). Reads from the process environment only."""
    return os.environ.get(env_var_name, default)


def get_connection_for_worker(service_name: str, security_context: str | None = None) -> dict[str, Any]:
    """Return the resolved Console Vault connection payload for a worker."""
    return dict(_fetch_connection(service_name, security_context=security_context))


def get_hubspot_connection(security_context: str | None = None) -> dict[str, Any]:
    """Return HubSpot connection material from env first, then Console Vault."""
    payload = get_connection_for_worker("hubspot", security_context=security_context)
    connection = dict(payload)
    env_token = ""
    for candidate in _ENV_ALIASES.get("HUBSPOT_API_TOKEN", ("HUBSPOT_API_TOKEN",)):
        value = os.environ.get(candidate)
        if value:
            env_token = value
            break
    connection["base_url"] = (
        get_secret_for_worker("hubspot", "HUBSPOT_BASE_URL", security_context=security_context)
        or settings.hubspot_base_url
    )
    auth_method = str(connection.get("auth_method") or "bearer_token").strip().lower()
    if env_token:
        connection["auth_method"] = "bearer_token"
        connection["token"] = env_token
        return connection
    if auth_method == "basic":
        user = get_secret_for_worker("hubspot", "HUBSPOT_USER", security_context=security_context)
        password = get_secret_for_worker("hubspot", "HUBSPOT_PASSWORD", security_context=security_context)
        if user:
            connection["user"] = user
        if password:
            connection["password"] = password
    else:
        token = get_secret_for_worker("hubspot", "HUBSPOT_API_TOKEN", security_context=security_context) or settings.hubspot_api_token or ""
        if token:
            if auth_method in {"api_key", "apikey", "x_api_key"}:
                connection.setdefault("api_key", token)
            else:
                connection.setdefault("token", token)
    connection.setdefault("auth_method", auth_method)
    return connection


def get_hubspot_credentials() -> tuple[str, str]:
    """Return (base_url, token) from environment or Console Vault."""
    connection = get_hubspot_connection()
    base_url = str(connection.get("base_url") or "")
    token = (
        str(connection.get("token") or "")
        or str(connection.get("api_token") or "")
        or str(connection.get("api_key") or "")
        or str(connection.get("password") or "")
    )
    if not token:
        raise ValueError(
            "HubSpot API token not configured.\n"
            "Set HUBSPOT_API_TOKEN or save connections/hubspot/default in Vault."
        )
    return base_url, token
