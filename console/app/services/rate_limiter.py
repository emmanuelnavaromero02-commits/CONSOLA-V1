"""
Rate-limiter backends for the console.

Two implementations are provided:

  - InMemoryRateLimiter — per-process sliding window. Sufficient for a single
    replica; behind multiple replicas an attacker can spread requests across
    pods to multiply the effective limit.
  - RedisRateLimiter — shared fixed-window counters via INCR+EXPIRE. Atomic
    per-key, accurate across replicas. Selected automatically when REDIS_URL
    is set in the environment.

`get_rate_limiter()` performs the selection. In development, it falls back to
the in-memory implementation if Redis is not configured. In production,
REDIS_URL is mandatory because in-memory buckets are per-process and bypassable
behind multiple replicas.

Failure policy: `check(..., sensitive=True)` fails CLOSED — if Redis is
unreachable for a sensitive endpoint (login / password reset / token refresh),
the request is denied so brute-force protection is never silently disabled.
Non-sensitive endpoints fail open to keep the service reachable under a Redis
outage.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Protocol


logger = logging.getLogger(__name__)


def _is_production() -> bool:
    # v1.43.2 (Codex P1-2): default ``production`` — unset APP_ENV must
    # not silently disable production guardrails. Local dev compose sets
    # APP_ENV=development explicitly.
    return os.environ.get("APP_ENV", "production").lower() in {"production", "prod"}


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        """Return True if the request is allowed, False if it must be denied."""
        ...


class InMemoryRateLimiter:
    # Run an opportunistic sweep every N check() calls so dict keys for IPs
    # that stop sending traffic don't accumulate forever. The sweep cost is
    # O(keys), amortised once per CLEANUP_INTERVAL — cheap compared to the
    # per-request work the FastAPI app already does.
    CLEANUP_INTERVAL = 1000

    def __init__(self) -> None:
        # Per-key list of monotonic timestamps for a sliding window.
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
        # 24h is comfortably larger than any window we register, so any bucket
        # whose newest hit is older than this is guaranteed to be stale for
        # every active limit. Conservative on purpose.
        stale_after = 60 * 60 * 24
        empty_keys = [
            k for k, hits in self._buckets.items()
            if not hits or now - hits[-1] > stale_after
        ]
        for k in empty_keys:
            self._buckets.pop(k, None)


class RedisRateLimiter:
    """
    Fixed-window counter in Redis. Each (key, window-bucket) gets a counter
    that is incremented atomically; the first writer also sets the TTL. Drift
    between windows is bounded by the window size, which is acceptable for
    auth endpoints where windows are minutes-long.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def check(self, key: str, limit: int, window: int, sensitive: bool = False) -> bool:
        # Bucketize wall time so all replicas agree on the current window.
        bucket = int(time.time() // window)
        redis_key = f"rl:{key}:{bucket}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                # First write in the window owns the TTL.
                await self._redis.expire(redis_key, window)
        except Exception:
            if sensitive:
                # Fail closed: a silently-disabled rate limit on /auth/* is
                # worse than a 503 for the duration of the Redis outage.
                logger.error("RedisRateLimiter unavailable; denying sensitive request", exc_info=True)
                return False
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
    """Test helper — forces the next get_rate_limiter() call to re-select."""
    global _LIMITER
    _LIMITER = None
