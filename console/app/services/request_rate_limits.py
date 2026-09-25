from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException, Request

from app.services.rate_limiter import get_rate_limiter


RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMITS = {
    "/auth/login": (8, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/forgot-password": (5, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/reset-password": (8, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/activate": (8, RATE_LIMIT_WINDOW_SECONDS),
    "/auth/refresh": (60, RATE_LIMIT_WINDOW_SECONDS),
    "/api/copilot": (120, 60),
    "/api/agents": (80, 60),
    "/api/mcp": (80, 60),
    "/studio/import": (10, RATE_LIMIT_WINDOW_SECONDS),
    "/api/explorer": (180, 60),
    "/apps/content": (60, 60),
}
API_RATE_LIMIT_PREFIXES = (
    "/api/copilot",
    "/api/agents",
    "/api/mcp",
    "/studio/import",
    "/api/explorer",
)


def trusted_proxy_ips(env: Mapping[str, str] | None = None) -> frozenset[str]:
    values = env if env is not None else os.environ
    return frozenset(
        ip.strip()
        for ip in values.get("TRUSTED_PROXY_IPS", "").split(",")
        if ip.strip()
    )


def client_ip(
    request: Request,
    *,
    trusted_proxies: frozenset[str] | None = None,
) -> str:
    real_ip = request.client.host if request.client else "unknown"
    proxies = trusted_proxy_ips() if trusted_proxies is None else trusted_proxies
    if proxies and real_ip in proxies:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
    return real_ip


def rate_limit_disabled(env: Mapping[str, str] | None = None) -> bool:
    values = env if env is not None else os.environ
    enabled_env = values.get("RATE_LIMIT_ENABLED")
    if enabled_env is not None and enabled_env.strip().lower() in {
        "false",
        "0",
        "no",
        "off",
    }:
        return True
    app_env = values.get("APP_ENV", "production").strip().lower()
    return app_env in {"test", "testing"}


def api_rate_limit_action(path: str) -> str | None:
    for prefix in API_RATE_LIMIT_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return prefix
    return None


async def rate_limit(
    request: Request,
    action: str,
    subject: str = "",
    *,
    limiter_factory: Callable[[], Any] = get_rate_limiter,
    trusted_proxies: frozenset[str] | None = None,
) -> None:
    if rate_limit_disabled():
        return

    limit, window = RATE_LIMITS[action]
    ip = client_ip(request, trusted_proxies=trusted_proxies)
    subject_key = subject.lower().strip() or "-"
    limiter = limiter_factory()
    keys = dict.fromkeys((f"{action}:{ip}:{subject_key}", f"{action}:{ip}:-"))
    for key in keys:
        if not await limiter.check(key, limit, window, sensitive=True):
            raise HTTPException(status_code=429, detail="too many requests")


async def rate_limit_api_surface(
    request: Request,
    path: str,
    user: dict | None,
    *,
    limiter_factory: Callable[[], Any] = get_rate_limiter,
    trusted_proxies: frozenset[str] | None = None,
) -> None:
    if rate_limit_disabled():
        return
    matched = api_rate_limit_action(path)
    if not matched:
        return
    limit, window = RATE_LIMITS[matched]
    ip = client_ip(request, trusted_proxies=trusted_proxies)
    user_key = str((user or {}).get("id") or (user or {}).get("email") or "-")
    limiter = limiter_factory()
    keys = dict.fromkeys((f"{matched}:{ip}:{user_key}", f"{matched}:{ip}:-"))
    for key in keys:
        if not await limiter.check(key, limit, window, sensitive=True):
            raise HTTPException(status_code=429, detail="too many requests")


async def rate_limit_app_content_capability(
    claims: Mapping[str, Any],
    *,
    limiter_factory: Callable[[], Any] = get_rate_limiter,
) -> None:
    if rate_limit_disabled():
        return
    user_key = str(claims.get("user") or "")
    if not user_key:
        raise HTTPException(status_code=403, detail="app content is not available")
    limit, window = RATE_LIMITS["/apps/content"]
    limiter = limiter_factory()
    if not await limiter.check(
        f"/apps/content:user:{user_key}",
        limit,
        window,
        sensitive=True,
    ):
        raise HTTPException(status_code=429, detail="too many requests")
