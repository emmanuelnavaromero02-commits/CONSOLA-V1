from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

import httpx


logger = logging.getLogger("app.main")
READYZ_DEPENDENCY_CACHE: dict[str, dict[str, Any]] = {}


def float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def readyz_dependency_cache_key(name: str, url: str) -> str:
    return f"{name}:{url}"


def readyz_dependency_cache_ttl() -> float:
    return max(0.0, float_env("READYZ_DEPENDENCY_CACHE_TTL_SECONDS", 15.0))


def readyz_dependency_stale_ttl() -> float:
    return max(0.0, float_env("READYZ_DEPENDENCY_STALE_TTL_SECONDS", 600.0))


def readyz_dependency_timeout() -> float:
    return max(0.5, float_env("READYZ_DEPENDENCY_TIMEOUT_SECONDS", 30.0))


async def dependency_health(
    name: str,
    url: str,
    server: str | None = None,
    *,
    header_factory: Callable[[str], dict[str, str]] | None = None,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
    monotonic: Callable[[], float] = time.monotonic,
    log: logging.Logger = logger,
) -> dict[str, Any]:
    if not url:
        return {"status": "down", "error": "missing_url"}
    cache_key = readyz_dependency_cache_key(name, url)
    now = monotonic()
    cached = READYZ_DEPENDENCY_CACHE.get(cache_key)
    if cached and now - float(cached.get("checked_at", 0.0)) <= readyz_dependency_cache_ttl():
        return dict(cached["result"])
    try:
        headers = header_factory(server) if server and header_factory else {}
        async with http_client_factory(
            headers=headers, timeout=readyz_dependency_timeout()
        ) as client:
            response = await client.get(url)
        status = "up" if response.status_code < 500 else "down"
        result = {"status": status, "code": response.status_code}
        READYZ_DEPENDENCY_CACHE[cache_key] = {
            "checked_at": now,
            "result": result,
            "last_up_at": now if status == "up" else (cached or {}).get("last_up_at"),
            "last_up_result": result
            if status == "up"
            else (cached or {}).get("last_up_result"),
        }
        return dict(result)
    except Exception as exc:
        log.warning("readiness probe failed for %s", name, exc_info=True)
        if (
            cached
            and cached.get("last_up_result")
            and now - float(cached.get("last_up_at", 0.0)) <= readyz_dependency_stale_ttl()
        ):
            result = dict(cached["last_up_result"])
            result["cached"] = True
            result["stale"] = True
            result["last_error"] = type(exc).__name__
            READYZ_DEPENDENCY_CACHE[cache_key] = {
                **cached,
                "checked_at": now,
                "result": result,
            }
            return result
        result = {"status": "down", "error": type(exc).__name__}
        READYZ_DEPENDENCY_CACHE[cache_key] = {
            "checked_at": now,
            "result": result,
            "last_up_at": (cached or {}).get("last_up_at"),
            "last_up_result": (cached or {}).get("last_up_result"),
        }
        return result
