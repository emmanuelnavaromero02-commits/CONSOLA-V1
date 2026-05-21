from __future__ import annotations

import logging
import os
import time
from typing import Protocol


logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = {}

    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        now = time.monotonic()
        hits = [ts for ts in self._buckets.get(key, []) if now - ts < window]
        if len(hits) >= limit:
            self._buckets[key] = hits
            return False
        hits.append(now)
        self._buckets[key] = hits
        return True


class RedisRateLimiter:
    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        bucket = int(time.time() // window)
        redis_key = f"workspace:rl:{key}:{bucket}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                await self._redis.expire(redis_key, window)
        except Exception:
            if sensitive:
                logger.error("workspace Redis rate limiter unavailable", exc_info=True)
                return False
            logger.warning("workspace Redis rate limiter unavailable; allowing request", exc_info=True)
            return True
        return count <= limit


_LIMITER: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is not None:
        return _LIMITER
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if _is_production() and not redis_url:
        raise RuntimeError("REDIS_URL is required in production for workspace rate limiting")
    if redis_url:
        try:
            from redis import asyncio as redis_async  # type: ignore

            _LIMITER = RedisRateLimiter(redis_async.from_url(redis_url, decode_responses=True))
            return _LIMITER
        except ImportError:
            if _is_production():
                raise RuntimeError("redis-py is required in production when REDIS_URL is set")
            logger.warning("redis-py unavailable; workspace falling back to in-memory limiter")
    _LIMITER = InMemoryRateLimiter()
    return _LIMITER
