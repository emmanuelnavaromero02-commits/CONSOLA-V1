from __future__ import annotations

import os
import secrets

from fastapi.responses import JSONResponse

INSECURE_DEFAULTS = frozenset({"dev-secret-key", "changeme", "secret", ""})
ALLOWED_INTERNAL_SERVICES = {"console", "workspace", "refinement", "mcp-infra", "airflow"}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key or any(secrets.compare_digest(key, bad) for bad in INSECURE_DEFAULTS):
        raise RuntimeError("INTERNAL_API_KEY is not configured or uses insecure default")
    return key


def _accepted_keys(service: str | None) -> list[str]:
    keys: list[str] = []
    if service == "console":
        keys.append(os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE", ""))
    if service == "airflow":
        keys.append(os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE", ""))
    if not _is_production():
        keys.append(get_internal_api_key())
    return [key for key in keys if key]


def is_valid_internal_request(api_key: str | None, service: str | None) -> bool:
    if not service or service not in ALLOWED_INTERNAL_SERVICES:
        return False
    return bool(api_key and any(secrets.compare_digest(api_key, key) for key in _accepted_keys(service)))


class InternalApiKeyASGIGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            headers = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])
            }
            if not is_valid_internal_request(headers.get("x-api-key"), headers.get("x-internal-service")):
                response = JSONResponse({"detail": "Missing or invalid X-Api-Key"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
