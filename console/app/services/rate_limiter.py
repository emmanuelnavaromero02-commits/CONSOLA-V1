from __future__ import annotations

import logging
import os
import time
from typing import Protocol


logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").lower() in {"production", "prod"}


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        ...


class InMemoryRateLimiter:
    CLEANUP_INTERVAL = 1000

    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = {}
        self._calls_since_cleanup = 0

    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        now = time.monotonic()
        self._maybe_cleanup(now)
        hits = [ts for ts in self._buckets.get(key, []) if now - ts < window]
        if len(hits) >= limit:
            self._buckets[key] = hits
            return False
        hits.append(now)
        self._buckets[key] = hits
        return True

    def _maybe_cleanup(self, now: float) -> None:
        self._calls_since_cleanup += 1
        if self._calls_since_cleanup < self.CLEANUP_INTERVAL:
            return
        self._calls_since_cleanup = 0
        stale_after = 60 * 60 * 24
        empty_keys = [
            k for k, hits in self._buckets.items()
            if not hits or now - hits[-1] > stale_after
        ]
        for k in empty_keys:
            self._buckets.pop(k, None)


class RedisRateLimiter:

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        bucket = int(time.time() // window)
        redis_key = f"rl:{key}:{bucket}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                await self._redis.expire(redis_key, window)
        except Exception:
            if sensitive:
                logger.error("RedisRateLimiter unavailable; denying sensitive request", exc_info=True)
                return False
            logger.warning("RedisRateLimiter unavailable; allowing request", exc_info=True)
            return True
        return count <= limit


_LIMITER: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is not None:
        return _LIMITER

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if _is_production() and not redis_url:
        raise RuntimeError("REDIS_URL is required in production for shared rate limiting")
    if redis_url:
        try:
            from redis import asyncio as redis_async  # type: ignore

            client = redis_async.from_url(redis_url, decode_responses=True)
            _LIMITER = RedisRateLimiter(client)
            logger.info("rate limiter: using Redis backend")
            return _LIMITER
        except ImportError:
            if _is_production():
                raise RuntimeError("redis-py is required in production when REDIS_URL is set")
            logger.warning("REDIS_URL set but redis-py not installed; using in-memory limiter")
        except Exception:
            if _is_production():
                raise
            logger.warning("REDIS_URL set but client init failed; using in-memory limiter", exc_info=True)

    _LIMITER = InMemoryRateLimiter()
    return _LIMITER


def reset_rate_limiter() -> None:
    global _LIMITER
    _LIMITER = None
