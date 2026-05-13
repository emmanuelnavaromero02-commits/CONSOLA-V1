"""JWT blacklist via Redis. Invalidates access tokens at logout.

Hooked into:
  - POST /auth/logout in main.py (writes the jti → blacklist with TTL = exp).
  - verify_access_token_async in jwt_auth.py (reads the blacklist on every
    JWT-authenticated request).

Fail-open: if Redis is unreachable or the key fetch raises, both revoke()
and is_revoked() return False. That keeps the system available — the
worst case is a window of up-to-exp seconds where a revoked token still
works, which matches the pre-blacklist behaviour. Denying every
JWT-authenticated request because Redis is down would be a much worse
failure mode.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_BLACKLIST_KEY_PREFIX = "jwt_blacklist:"
# Minimum TTL applied even if exp is already in the past. Protects against
# clock-skew between the auth issuer and Redis, and gives us a small grace
# window for in-flight requests already past the auth check.
_MIN_TTL_SECONDS = 60


class _BlacklistBackend:
    """Thin wrapper over redis-py async client. One instance per process."""

    def __init__(self) -> None:
        self._client = None
        self._init_attempted = False

    def _get_client(self):
        if self._init_attempted:
            return self._client
        self._init_attempted = True
        url = os.environ.get("REDIS_URL", "").strip()
        if not url:
            return None
        try:
            from redis import asyncio as redis_async  # type: ignore
        except ImportError:
            logger.warning("jwt_blacklist: redis-py not installed, falling back to no-op")
            return None
        try:
            self._client = redis_async.from_url(url, decode_responses=True)
            logger.info("jwt_blacklist: Redis backend connected")
        except Exception:
            logger.warning(
                "jwt_blacklist: Redis init failed, falling back to no-op",
                exc_info=True,
            )
            self._client = None
        return self._client

    async def revoke(self, jti: str, exp_unix: int) -> bool:
        """Mark jti as revoked. Returns True iff the key was written."""
        if not jti:
            return False
        client = self._get_client()
        if client is None:
            return False
        ttl = max(int(exp_unix - time.time()), _MIN_TTL_SECONDS)
        try:
            await client.setex(f"{_BLACKLIST_KEY_PREFIX}{jti}", ttl, "1")
            return True
        except Exception:
            logger.warning(
                "jwt_blacklist.revoke failed for jti=%s", jti, exc_info=True
            )
            return False

    async def is_revoked(self, jti: str) -> bool:
        """Return True iff the jti is in the blacklist. Fail-open on errors."""
        if not jti:
            return False
        client = self._get_client()
        if client is None:
            return False  # fail-open: no Redis configured
        try:
            value = await client.get(f"{_BLACKLIST_KEY_PREFIX}{jti}")
            return value is not None
        except Exception:
            logger.warning(
                "jwt_blacklist.is_revoked failed for jti=%s", jti, exc_info=True
            )
            return False  # fail-open: Redis crash must not 401 everything


_BACKEND: Optional[_BlacklistBackend] = None


def get_blacklist() -> _BlacklistBackend:
    """Module-level singleton accessor."""
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _BlacklistBackend()
    return _BACKEND


def reset_blacklist() -> None:
    """Test hook — clears the cached backend so the next call rebuilds it."""
    global _BACKEND
    _BACKEND = None
