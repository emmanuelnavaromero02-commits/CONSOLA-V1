from __future__ import annotations

import os
import secrets
from collections.abc import Mapping

from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse


ALLOWED_INTERNAL_SERVICES = {
    "console",
    "workspace",
    "refinement",
    "mcp-infra",
    "airflow",
}


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY")
    insecure_default = "dev" + "-secret-key"
    if not key or secrets.compare_digest(key, insecure_default):
        raise RuntimeError("INTERNAL_API_KEY missing or using an insecure default. System halted for security.")
    return key


INTERNAL_API_KEY = get_internal_api_key()


def _is_valid_internal_request(x_api_key: str | None, x_internal_service: str | None) -> bool:
    if not x_internal_service or x_internal_service not in ALLOWED_INTERNAL_SERVICES:
        return False
    return bool(x_api_key and secrets.compare_digest(x_api_key, INTERNAL_API_KEY))


def verify_api_key(
    x_api_key: str | None = Header(None),
    x_internal_service: str | None = Header(None),
) -> None:
    if not _is_valid_internal_request(x_api_key, x_internal_service):
        raise HTTPException(status_code=403, detail="Forbidden")


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
                response = JSONResponse({"detail": "Forbidden"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
