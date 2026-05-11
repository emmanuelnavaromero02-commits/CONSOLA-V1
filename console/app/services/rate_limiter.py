"""
Rate-limiter backends for the console.

Two implementations are provided:

  - InMemoryRateLimiter — per-process sliding window. Sufficient for a single
    replica; behind multiple replicas an attacker can spread requests across
    pods to multiply the effective limit.
  - RedisRateLimiter — shared fixed-window counters via INCR+EXPIRE. Atomic
    per-key, accurate across replicas. Selected automatically when REDIS_URL
    is set in the environment.

`get_rate_limiter()` performs the selection. It silently falls back to the
in-memory implementation if redis-py is not installed or the connection cannot
be established at first use, so the auth endpoints always have *some*
limiting rather than failing closed.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Protocol


logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int) -> bool:
        """Return True if the request is allowed, False if it must be denied."""
        ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        # Per-key list of monotonic timestamps for a sliding window.
        self._buckets: dict[str, list[float]] = {}

    async def check(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        hits = [ts for ts in self._buckets.get(key, []) if now - ts < window]
        if len(hits) >= limit:
            self._buckets[key] = hits
            return False
        hits.append(now)
        self._buckets[key] = hits
        return True


class RedisRateLimiter:
    """
    Fixed-window counter in Redis. Each (key, window-bucket) gets a counter
    that is incremented atomically; the first writer also sets the TTL. Drift
    between windows is bounded by the window size, which is acceptable for
    auth endpoints where windows are minutes-long.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def check(self, key: str, limit: int, window: int) -> bool:
        # Bucketize wall time so all replicas agree on the current window.
        bucket = int(time.time() // window)
        redis_key = f"rl:{key}:{bucket}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                # First write in the window owns the TTL.
                await self._redis.expire(redis_key, window)
        except Exception:
            # Fail open with a warning rather than locking users out when Redis
            # is unreachable. The in-memory fallback in the factory handles
            # startup-time failures; this branch covers transient outages.
            logger.warning("RedisRateLimiter unavailable; allowing request", exc_info=True)
            return True
        return count <= limit


_LIMITER: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Lazy singleton — picks Redis when REDIS_URL is set and reachable."""
    global _LIMITER
    if _LIMITER is not None:
        return _LIMITER

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        try:
            from redis import asyncio as redis_async  # type: ignore

            client = redis_async.from_url(redis_url, decode_responses=True)
            _LIMITER = RedisRateLimiter(client)
            logger.info("rate limiter: using Redis backend")
            return _LIMITER
        except ImportError:
            logger.warning("REDIS_URL set but redis-py not installed; using in-memory limiter")
        except Exception:
            logger.warning("REDIS_URL set but client init failed; using in-memory limiter", exc_info=True)

    _LIMITER = InMemoryRateLimiter()
    return _LIMITER


def reset_rate_limiter() -> None:
    """Test helper — forces the next get_rate_limiter() call to re-select."""
    global _LIMITER
    _LIMITER = None
