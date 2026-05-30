from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_CONNECTION_CACHE: dict[str, dict[str, Any]] = {}

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
    "hubspot": ("hubspot", "cartridge-hubspot"),
    "sap_hcm": ("cartridge-sap_hcm",),
    "sap_s4hana": ("cartridge-sap_s4hana",),
    "sap_successfactors": ("cartridge-sap_successfactors",),
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _auth_options(service_name: str) -> list[tuple[str, str]]:
    service = service_name.strip().lower()
    options: list[tuple[str, str]] = []
    if service == "hubspot":
        key = os.environ.get("INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE", "")
        if key:
            options.append((key, "hubspot"))
    key = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "")
    if key:
        for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
            if header != "hubspot":
                options.append((key, header))
    if not options and not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
                options.append((legacy, header))
    return options


def _fetch_connection(service_name: str) -> dict[str, Any]:
    service = service_name.strip().lower()
    cached = _CONNECTION_CACHE.get(service)
    if cached is not None:
        return cached

    console_url = os.environ.get("CONSOLE_URL", "http://console:8000").rstrip("/")
    for conn_id in ("default", "analytics"):
        for key, internal_service in _auth_options(service):
            try:
                response = requests.get(
                    f"{console_url}/api/vault/connections/{service}/{conn_id}/reveal",
                    headers={"x-api-key": key, "x-internal-service": internal_service},
                    timeout=5,
                )
                if response.status_code == 404:
                    break
                if response.status_code in {401, 403}:
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    _CONNECTION_CACHE[service] = payload
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


def get_secret_for_worker(service_name: str, env_var_name: str) -> str:
    """Resolve a worker credential from env first, then Console Vault."""
    for candidate in _ENV_ALIASES.get(env_var_name, (env_var_name,)):
        value = os.environ.get(candidate)
        if value:
            return value

    payload = _fetch_connection(service_name)
    for field in _candidate_fields(env_var_name):
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def get_secret(env_var_name: str, default: str = "") -> str:
    """Single-arg secret access used by protection_service (mirrors the SAP
    cartridge signature). Reads from the process environment only."""
    return os.environ.get(env_var_name, default)


def get_connection_for_worker(service_name: str) -> dict[str, Any]:
    """Return the resolved Console Vault connection payload for a worker."""
    return dict(_fetch_connection(service_name))


def get_hubspot_connection() -> dict[str, Any]:
    """Return HubSpot connection material from env first, then Console Vault."""
    payload = get_connection_for_worker("hubspot")
    connection = dict(payload)
    connection["base_url"] = (
        get_secret_for_worker("hubspot", "HUBSPOT_BASE_URL")
        or settings.hubspot_base_url
    )
    auth_method = str(connection.get("auth_method") or "bearer_token").strip().lower()
    if auth_method == "basic":
        user = get_secret_for_worker("hubspot", "HUBSPOT_USER")
        password = get_secret_for_worker("hubspot", "HUBSPOT_PASSWORD")
        if user:
            connection["user"] = user
        if password:
            connection["password"] = password
    else:
        token = get_secret_for_worker("hubspot", "HUBSPOT_API_TOKEN") or settings.hubspot_api_token or ""
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
