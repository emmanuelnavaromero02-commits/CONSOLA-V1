from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_CONNECTION_CACHE: dict[str, dict[str, Any]] = {}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "SAP_S4_BASE_URL": ("base_url", "url", "host", "sap_s4_base_url"),
    "SAP_S4_USER": ("user", "username", "sap_s4_user"),
    "SAP_S4_PASS": ("password", "pass", "sap_s4_pass"),
    "SAP_S4_CLIENT_MANDANT": ("client_mandant", "mandant", "client", "sap_s4_client_mandant"),
    "SAP_S4_API_KEY": ("api_key", "apikey", "token", "sap_s4_api_key"),
}

_SERVICE_HEADERS: dict[str, tuple[str, ...]] = {
    "replicon": ("replicon", "cartridge-replicon"),
    "sap_hcm": ("cartridge-sap_hcm",),
    "sap_s4hana": ("cartridge-sap_s4hana",),
    "sap_successfactors": ("cartridge-sap_successfactors",),
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _auth_options(service_name: str) -> list[tuple[str, str]]:
    service = service_name.strip().lower()
    options: list[tuple[str, str]] = []
    if service == "replicon":
        key = os.environ.get("INTERNAL_API_KEY_REPLICON_TO_CONSOLE", "")
        if key:
            options.append((key, "replicon"))
    key = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "")
    if key:
        for header in _SERVICE_HEADERS.get(service, (f"cartridge-{service}",)):
            if header != "replicon":
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
    fields: list[str] = []
    fields.extend(_FIELD_ALIASES.get(direct, ()))
    fields.extend((direct, direct.lower()))
    for prefix in ("REPLICON_", "SAP_HCM_", "SAP_S4_", "SF_"):
        if direct.startswith(prefix):
            fields.append(direct.removeprefix(prefix).lower())
    return tuple(dict.fromkeys(fields))


def get_secret_for_worker(service_name: str, env_var_name: str) -> str:
    """Resolve a worker credential from env first, then Console Vault."""
    value = os.environ.get(env_var_name)
    if value:
        return value

    payload = _fetch_connection(service_name)
    for field in _candidate_fields(env_var_name):
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def get_sap_s4hana_credentials() -> tuple[str, str, str]:
    """Return (base_url, user, pass) from environment or Console Vault."""
    base_url = get_secret_for_worker("sap_s4hana", "SAP_S4_BASE_URL") or settings.sap_s4_base_url
    user = get_secret_for_worker("sap_s4hana", "SAP_S4_USER") or settings.sap_s4_user
    password = get_secret_for_worker("sap_s4hana", "SAP_S4_PASS") or settings.sap_s4_pass

    if not user or not password:
        raise ValueError(
            "SAP S/4HANA credentials not configured.\n"
            "Set SAP_S4_USER/SAP_S4_PASS or save connections/sap_s4hana/default in Vault."
        )
    return base_url, user, password


def get_secret(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
