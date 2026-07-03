"""Request rate-limit helpers for console HTTP surfaces."""

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
    # Refresh is more frequent than login (access tokens expire in minutes), so
    # the cap is higher; still bounded to deter token-stuffing brute force.
    "/auth/refresh": (60, RATE_LIMIT_WINDOW_SECONDS),
    "/api/copilot": (120, 60),
    "/api/agents": (80, 60),
    "/api/mcp": (80, 60),
    "/studio/import": (10, RATE_LIMIT_WINDOW_SECONDS),
    "/api/explorer": (180, 60),
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
    # Only trust X-Forwarded-For when the direct connection comes from a declared proxy.
    if proxies and real_ip in proxies:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
    return real_ip


def rate_limit_disabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True when rate limiting should bypass.

    The bypass fires in two scenarios — both are EXPLICITLY
    test-harness affordances, never production behaviour:

      1. ``RATE_LIMIT_ENABLED=false`` (any case) — an explicit
         opt-out for E2E suites that hammer /auth/login. Default
         unset -> enabled.
      2. ``APP_ENV`` in {``test``, ``testing``} — automatic for
         pytest harnesses that don't bother setting
         RATE_LIMIT_ENABLED.
    """
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
    # Two checks both must pass:
    #   1) per (ip, subject) — keeps a noisy single user from drowning others
    #   2) per ip — prevents subject-rotation bypass.
    limiter = limiter_factory()
    for key in (f"{action}:{ip}:{subject_key}", f"{action}:{ip}:-"):
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
    for key in (f"{matched}:{ip}:{user_key}", f"{matched}:{ip}:-"):
        if not await limiter.check(key, limit, window, sensitive=True):
            raise HTTPException(status_code=429, detail="too many requests")
