from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from app.services.control_room.cache_identity import (
    AuthorizationCacheIdentity,
    authorization_cache_identity,
)

CacheKey = tuple[str, AuthorizationCacheIdentity]
Loader = Callable[[], Awaitable[Any]]

READ_CACHE: dict[CacheKey, tuple[float, Any]] = {}
READ_CACHE_LOCKS: dict[CacheKey, asyncio.Lock] = {}


def cache_ttl() -> float:
    raw = os.environ.get("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "15")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 15.0
    return max(0.0, min(value, 300.0))


def cache_key(namespace: str, user: dict | None) -> CacheKey:
    return namespace, authorization_cache_identity(user)


def _cache_get_key(key: CacheKey) -> Any | None:
    cached = READ_CACHE.get(key)
    if not cached:
        return None
    expires_at, value = cached
    if expires_at <= time.monotonic():
        READ_CACHE.pop(key, None)
        return None
    return deepcopy(value)


def _cache_set_key(key: CacheKey, value: Any, ttl: float) -> Any:
    if ttl > 0:
        READ_CACHE[key] = (
            time.monotonic() + ttl,
            deepcopy(value),
        )
    return value


def cache_get(namespace: str, user: dict | None) -> Any | None:
    if cache_ttl() <= 0:
        return None
    return _cache_get_key(cache_key(namespace, user))


def cache_set(namespace: str, user: dict | None, value: Any) -> Any:
    ttl = cache_ttl()
    return _cache_set_key(cache_key(namespace, user), value, ttl)


async def cache_get_or_set(
    namespace: str,
    user: dict | None,
    loader: Loader,
) -> Any:
    ttl = cache_ttl()
    if ttl <= 0:
        return await loader()
    key = cache_key(namespace, user)
    cached = _cache_get_key(key)
    if cached is not None:
        return cached
    lock = READ_CACHE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache_get_key(key)
        if cached is not None:
            return cached
        return _cache_set_key(key, await loader(), ttl)


def cache_invalidate(user: dict | None) -> None:
    identity = authorization_cache_identity(user)
    for key in [key for key in READ_CACHE if key[1] == identity]:
        READ_CACHE.pop(key, None)
