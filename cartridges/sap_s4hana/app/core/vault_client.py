from __future__ import annotations

import logging
import os
import hashlib
import json
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_CONNECTION_CACHE: dict[tuple[str, str], dict[str, Any]] = {}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "SAP_S4_BASE_URL": ("base_url", "url", "host", "sap_s4_base_url"),
    "SAP_S4_USER": ("user", "username", "sap_s4_user"),
    "SAP_S4_PASS": ("password", "pass", "sap_s4_pass"),
    "SAP_S4_CLIENT_MANDANT": ("client_mandant", "mandant", "client", "sap_s4_client_mandant"),
    "SAP_S4_API_KEY": ("api_key", "apikey", "token", "sap_s4_api_key"),
    "SAP_S4_TOKEN": ("token", "api_token", "api_key", "sap_s4_token"),
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


def get_sap_s4hana_credentials() -> tuple[str, str, str]:
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
