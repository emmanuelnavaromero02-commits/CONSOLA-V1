from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any


_BEARER = {"bearer", "bearer_token", "token"}
_API_KEY = {"api_key", "apikey", "x_api_key"}
_BASIC = {"basic", "basic_auth"}
_NONE = {"none", "no_auth", "anonymous"}


def normalize_auth_method(value: Any, default: str = "bearer_token") -> str:
    raw = str(value or default).strip().lower().replace("-", "_")
    if raw in _BEARER:
        return "bearer_token"
    if raw in _API_KEY:
        return "api_key"
    if raw in _BASIC:
        return "basic"
    if raw in _NONE:
        return "none"
    raise ValueError(f"Unsupported auth_method: {value}")


def _first(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def build_auth_headers(
    payload: Mapping[str, Any],
    *,
    default_method: str = "bearer_token",
    default_api_key_header: str = "X-API-Key",
    base_headers: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], str, tuple[str, ...]]:
    method = normalize_auth_method(payload.get("auth_method"), default_method)
    headers = dict(base_headers or {})

    if method == "none":
        return headers, method, ()

    if method == "bearer_token":
        token = _first(payload, "token", "access_token", "api_token", "api_key", "password")
        if not token:
            raise ValueError("auth_method=bearer_token requires token/api_token/api_key")
        headers["Authorization"] = f"Bearer {token}"
        return headers, method, ("Authorization",)

    if method == "api_key":
        token = _first(payload, "api_key", "token", "access_token", "api_token", "password")
        if not token:
            raise ValueError("auth_method=api_key requires api_key/token")
        header_name = _first(payload, "api_key_header", "header", "header_name") or default_api_key_header
        headers[header_name] = token
        return headers, method, (header_name,)

    user = _first(payload, "user", "username", "client_id")
    password = _first(payload, "password", "pass", "token", "client_secret")
    if not user or not password:
        raise ValueError("auth_method=basic requires user/username and password")
    encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    headers["Authorization"] = f"Basic {encoded}"
    return headers, method, ("Authorization",)


def auth_trace(method: str, header_names: tuple[str, ...]) -> str:
    generated = ", ".join(header_names) if header_names else "(sin header)"
    return f"Autenticando con {method} -> Header generado -> {generated}"
