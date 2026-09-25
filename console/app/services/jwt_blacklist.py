from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_BLACKLIST_KEY_PREFIX = "jwt_blacklist:"
_MIN_TTL_SECONDS = 60
_FALSEY = {"0", "false", "no", "off"}
_TRUTHY = {"1", "true", "yes", "on"}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _fail_closed_enabled() -> bool:
    raw = os.environ.get("JWT_BLACKLIST_FAIL_CLOSED")
    if raw is not None:
        value = raw.strip().lower()
        if value in _FALSEY:
            return False
        if value in _TRUTHY:
            return True
    return _is_production()


class _BlacklistBackend:

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
        if not jti:
            return False
        client = self._get_client()
        if client is None:
            if _fail_closed_enabled():
                logger.error("jwt_blacklist unavailable; denying JWT request")
                return True
            return False
        try:
            value = await client.get(f"{_BLACKLIST_KEY_PREFIX}{jti}")
            return value is not None
        except Exception:
            logger.warning(
                "jwt_blacklist.is_revoked failed for jti=%s", jti, exc_info=True
            )
            if _fail_closed_enabled():
                logger.error("jwt_blacklist Redis check failed; denying JWT request")
                return True
            return False


_BACKEND: Optional[_BlacklistBackend] = None


def get_blacklist() -> _BlacklistBackend:
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _BlacklistBackend()
    return _BACKEND


def reset_blacklist() -> None:
    global _BACKEND
    _BACKEND = None
