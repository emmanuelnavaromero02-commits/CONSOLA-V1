from __future__ import annotations

import pytest

from app.services import rate_limiter
from app.services.rate_limiter import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    get_rate_limiter,
    reset_rate_limiter,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.mark.asyncio
async def test_in_memory_allows_under_limit():
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        assert await limiter.check("k", limit=3, window=60) is True


@pytest.mark.asyncio
async def test_in_memory_denies_over_limit():
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        await limiter.check("k", limit=3, window=60)
    assert await limiter.check("k", limit=3, window=60) is False


@pytest.mark.asyncio
async def test_in_memory_isolates_keys():
    limiter = InMemoryRateLimiter()
    for _ in range(2):
        await limiter.check("a", limit=2, window=60)
    # Hitting a different key must not consume A's window.
    assert await limiter.check("b", limit=2, window=60) is True
    assert await limiter.check("a", limit=2, window=60) is False


def test_factory_returns_in_memory_when_no_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert isinstance(get_rate_limiter(), InMemoryRateLimiter)


def test_factory_falls_back_to_in_memory_when_redis_missing(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    # Simulate redis-py not installed.
    import builtins

    real_import = builtins.__import__

    def _block(name, *a, **kw):
        if name.startswith("redis"):
            raise ImportError("simulated missing redis")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _block)
    assert isinstance(get_rate_limiter(), InMemoryRateLimiter)


@pytest.mark.asyncio
async def test_redis_backend_uses_incr_and_expire():
    """Verifies the Redis backend honours the contract — first hit sets TTL,
    subsequent hits only INCR, and overflow returns False without raising."""

    class FakeRedis:
        def __init__(self):
            self.counts: dict[str, int] = {}
            self.expires: dict[str, int] = {}

        async def incr(self, k):
            self.counts[k] = self.counts.get(k, 0) + 1
            return self.counts[k]

        async def expire(self, k, ttl):
            self.expires[k] = ttl

    fake = FakeRedis()
    limiter = RedisRateLimiter(fake)

    assert await limiter.check("login", limit=2, window=60) is True
    assert await limiter.check("login", limit=2, window=60) is True
    assert await limiter.check("login", limit=2, window=60) is False
    # Exactly one expire call — the first one that opened the window.
    assert len(fake.expires) == 1
    assert next(iter(fake.expires.values())) == 60


@pytest.mark.asyncio
async def test_redis_backend_fails_open_on_error():
    class BrokenRedis:
        async def incr(self, k):
            raise ConnectionError("boom")

        async def expire(self, k, ttl):
            pass

    limiter = RedisRateLimiter(BrokenRedis())
    # Non-sensitive endpoints stay reachable when Redis is down.
    assert await limiter.check("non-sensitive", limit=1, window=60) is True


@pytest.mark.asyncio
async def test_redis_backend_fails_closed_for_sensitive_on_error():
    class BrokenRedis:
        async def incr(self, k):
            raise ConnectionError("boom")

        async def expire(self, k, ttl):
            pass

    limiter = RedisRateLimiter(BrokenRedis())
    # Sensitive endpoints (login / refresh / reset) MUST deny when Redis is
    # unreachable — silently disabling rate limits on auth is unacceptable.
    assert await limiter.check("login", limit=1, window=60, sensitive=True) is False


@pytest.mark.asyncio
async def test_in_memory_accepts_sensitive_flag():
    # The in-memory backend cannot lose state on its own, so the flag is a
    # no-op for it; the contract is just that the call signature accepts it.
    limiter = InMemoryRateLimiter()
    assert await limiter.check("k", limit=2, window=60, sensitive=True) is True
    assert await limiter.check("k", limit=2, window=60, sensitive=True) is True
    assert await limiter.check("k", limit=2, window=60, sensitive=True) is False


@pytest.mark.asyncio
async def test_in_memory_cleans_up_stale_keys():
    """Without periodic cleanup the bucket dict grows forever as new
    (ip, subject) keys arrive. The sweep keys-empty buckets and any whose
    last hit is older than the conservative stale-after threshold."""
    limiter = InMemoryRateLimiter()
    # Force the cleanup interval to a small number for the test, then prime
    # buckets that should be considered stale.
    limiter.CLEANUP_INTERVAL = 5
    import time as _t
    fake_old = _t.monotonic() - (60 * 60 * 25)  # 25h ago
    for i in range(3):
        limiter._buckets[f"stale-{i}"] = [fake_old]
    # Trigger the sweep by issuing the threshold number of fresh checks.
    for i in range(limiter.CLEANUP_INTERVAL):
        await limiter.check(f"fresh-{i}", limit=10, window=60)
    assert all(k.startswith("fresh-") for k in limiter._buckets), \
        f"stale keys not cleaned up: {list(limiter._buckets)}"
