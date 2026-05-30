from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from app.security import get_internal_api_key


_DEFAULT_CONN_ID = "default"


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _service_url(env_name: str, docker_default: str, local_default: str) -> str:
    raw = os.environ.get(env_name)
    if raw:
        return raw.rstrip("/")
    return docker_default.rstrip("/") if _running_in_container() else local_default.rstrip("/")


def vault_url() -> str:
    return _service_url("VAULT_URL", "http://vault:8300", "http://127.0.0.1:8300")


def vault_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_VAULT")
    if not key and os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}:
        raise RuntimeError("Missing INTERNAL_API_KEY_CONSOLE_TO_VAULT; legacy fallback disabled in production")
    if not key:
        key = get_internal_api_key()
    return {"x-api-key": key, "x-internal-service": "console-worker"}


async def get_connection_for_worker(service_name: str, conn_id: str = _DEFAULT_CONN_ID) -> dict[str, Any]:
    service = str(service_name or "").strip()
    if not service:
        raise ValueError("service_name is required")
    async with httpx.AsyncClient(headers=vault_headers(), timeout=10.0) as client:
        response = await client.get(f"{vault_url()}/connections/{service}/{conn_id}")
    if response.status_code == 404:
        raise RuntimeError(f"Vault connection not found: connections/{service}/{conn_id}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Vault returned invalid connection payload for {service}/{conn_id}")
    return payload


async def get_secret_for_worker(service_name: str, env_var_name: str) -> str | None:
    env_value = os.environ.get(env_var_name)
    if env_value:
        return env_value
    connection = await get_connection_for_worker(service_name)
    for key in (
        env_var_name,
        env_var_name.lower(),
        env_var_name.replace(f"{service_name.upper()}_", "").lower(),
        "token",
        "api_token",
        "api_key",
        "password",
        "client_secret",
    ):
        value = connection.get(key)
        if value not in (None, ""):
            return str(value)
    return None
