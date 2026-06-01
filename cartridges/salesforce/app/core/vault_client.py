from __future__ import annotations

import logging
import os
from typing import Any

import requests

from app.core.config import settings
from app.core.settings_proxy import get_setting

logger = logging.getLogger(__name__)

_CONNECTION_CACHE: dict[str, dict[str, Any]] = {}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "SF_BASE_URL": ("base_url", "url", "host", "instance_url", "sf_base_url"),
    "SF_COMPANY_ID": ("company_id", "company", "sf_company_id"),
    "SF_CLIENT_ID": ("client_id", "consumer_key", "sf_client_id"),
    # NOTE: "password" is deliberately NOT an alias for the OAuth client secret.
    # In Salesforce's username-password flow the Connected App consumer secret
    # and the user's login password are distinct; a Vault field named
    # ``password`` is the login password (SF_PASSWORD), never the client secret.
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


def get_connection_for_worker(service_name: str) -> dict[str, Any]:
    """Return the resolved Console Vault connection payload for a worker."""
    return dict(_fetch_connection(service_name))


def get_salesforce_credentials() -> tuple[str, str, str, str, str]:
    """Return SF OAuth credentials from environment, Console Vault, or settings."""
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

    # Salesforce has no company_id (that was a SAP concept) — it is NOT required.
    # base_url + client_id + client_secret + token_url is the minimum for the
    # OAuth flows; company_id is returned only for tuple-shape parity and is
    # always "" for Salesforce.
    if not all([base_url, client_id, client_secret, token_url]):
        raise ValueError(
            "Salesforce credentials not configured.\n"
            "Set SF_* variables or save connections/salesforce/default in Vault."
        )
    return base_url, company_id, client_id, client_secret, token_url


def get_secret(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
