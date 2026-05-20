"""
Internal API key resolver.

The key is read on every call so that:
  * test suites can set the env var before importing the app;
  * a missing or insecure-default key hard-fails instead of returning a
    sentinel string that could be submitted back as an API key.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Mapping

from fastapi.responses import JSONResponse

INSECURE_DEFAULTS = frozenset({"dev-secret-key", "changeme", "secret", ""})
ALLOWED_INTERNAL_SERVICES = {
    "console",
    "workspace",
    "refinement",
    "mcp-infra",
    "airflow",
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key or any(secrets.compare_digest(key, bad) for bad in INSECURE_DEFAULTS):
        raise RuntimeError(
            "INTERNAL_API_KEY is not configured or uses insecure default. "
            "Refusing to start. Set INTERNAL_API_KEY to a strong secret."
        )
    return key


def _is_valid_internal_request(x_api_key: str | None, x_internal_service: str | None) -> bool:
    if not x_internal_service or x_internal_service not in ALLOWED_INTERNAL_SERVICES:
        return False
    accepted = []
    if x_internal_service == "console":
        pair = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE")
        if pair:
            accepted.append(pair)
    if not _is_production():
        accepted.append(get_internal_api_key())
    return bool(x_api_key and any(secrets.compare_digest(x_api_key, k) for k in accepted if k))


class InternalApiKeyASGIGuard:
    """ASGI guard for mounted sub-apps that cannot receive FastAPI dependencies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            headers: Mapping[str, str] = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])
            }
            if not _is_valid_internal_request(
                headers.get("x-api-key"),
                headers.get("x-internal-service"),
            ):
                if scope.get("type") == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                response = JSONResponse({"detail": "Missing or invalid X-Api-Key"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
