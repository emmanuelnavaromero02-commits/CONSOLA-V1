from __future__ import annotations

import base64
from typing import Any

from .base import AdapterConfigurationError


def _first(credentials: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = credentials.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def auth_headers(credentials: dict[str, Any]) -> dict[str, str]:
    method = str(credentials.get("auth_method") or credentials.get("auth") or "bearer_token").strip().lower()
    headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}

    if method in {"bearer", "bearer_token", "token"}:
        token = _first(credentials, "token", "api_token", "access_token", "REPLICON_API_TOKEN")
        if not token:
            raise AdapterConfigurationError("bearer_token authentication requires token")
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if method in {"api_key", "apikey"}:
        token = _first(credentials, "api_key", "token", "key")
        header_name = _first(credentials, "api_key_header", "header_name") or "X-API-Key"
        if not token:
            raise AdapterConfigurationError("api_key authentication requires api_key")
        headers[header_name] = token
        return headers

    if method in {"basic", "basic_auth", "password"}:
        username = _first(credentials, "username", "user", "client_id")
        password = _first(credentials, "password", "pass", "client_secret")
        if not username or not password:
            raise AdapterConfigurationError("basic authentication requires username/password")
        raw = f"{username}:{password}".encode("utf-8")
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
        return headers

    raise AdapterConfigurationError(f"unsupported auth_method: {method}")
