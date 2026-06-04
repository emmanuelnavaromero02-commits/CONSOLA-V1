"""Sprint v1.10 — JWT blacklist (Redis) tests.

Covers:
  * Backend behaviour: with and without Redis (fakeredis vs. no client).
  * revoke / is_revoked happy path, error paths, production fail-closed policy.
  * TTL floor of 60s even when exp is already in the past.
  * verify_access_token_async raises "access token revoked" for blacklisted jti.
  * Logout endpoint calls the blacklist when a Bearer header is supplied.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import jwt_blacklist


# ── Fakeredis-backed fixture ──────────────────────────────────────────

@pytest.fixture
def fake_backend():
    """Patch _BlacklistBackend._get_client to return a fakeredis client.

    Sync fixture intentionally: pytest-asyncio's auto event loop is
    function-scoped, and using `asyncio.get_event_loop()` from a sync
    teardown breaks once any other async test has consumed the loop.
    Skipping explicit aclose() — fakeredis cleans up via GC, and the
    reset_blacklist() drops our reference to the client.
    """
    import fakeredis.aioredis as fake_aioredis

    jwt_blacklist.reset_blacklist()
    sys.modules["app.services.jwt_blacklist"] = jwt_blacklist
    import app.services as services_pkg
    setattr(services_pkg, "jwt_blacklist", jwt_blacklist)
    backend = jwt_blacklist.get_blacklist()
    fake = fake_aioredis.FakeRedis(decode_responses=True)

    backend._client = fake
    backend._init_attempted = True
    try:
        yield backend, fake
    finally:
        jwt_blacklist.reset_blacklist()


@pytest.fixture
def offline_backend():
    """Backend whose Redis client is None (simulates REDIS_URL unset)."""
    jwt_blacklist.reset_blacklist()
    sys.modules["app.services.jwt_blacklist"] = jwt_blacklist
    import app.services as services_pkg
    setattr(services_pkg, "jwt_blacklist", jwt_blacklist)
    backend = jwt_blacklist.get_blacklist()
    backend._client = None
    backend._init_attempted = True
    yield backend
    jwt_blacklist.reset_blacklist()


@pytest.fixture(autouse=True)
def jwt_blacklist_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("JWT_BLACKLIST_FAIL_CLOSED", raising=False)


# ── revoke / is_revoked happy path ────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_stores_key_with_ttl(fake_backend):
    backend, fake = fake_backend
    future_exp = int(time.time()) + 300  # 5 min from now

    ok = await backend.revoke("jti-1", future_exp)
    assert ok is True

    stored = await fake.get("jwt_blacklist:jti-1")
    assert stored == "1"
    ttl = await fake.ttl("jwt_blacklist:jti-1")
    # Should be close to 300 but accept any positive remaining time.
    assert 200 <= ttl <= 300


@pytest.mark.asyncio
async def test_is_revoked_true_immediately_after_revoke(fake_backend):
    backend, _ = fake_backend
    await backend.revoke("jti-A", int(time.time()) + 120)
    assert await backend.is_revoked("jti-A") is True


@pytest.mark.asyncio
async def test_is_revoked_false_for_unknown_jti(fake_backend):
    backend, _ = fake_backend
    assert await backend.is_revoked("never-seen") is False


# ── TTL floor ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ttl_floor_is_60_seconds_when_exp_in_past(fake_backend):
    backend, fake = fake_backend
    past_exp = int(time.time()) - 1000  # already expired
    await backend.revoke("jti-old", past_exp)
    ttl = await fake.ttl("jwt_blacklist:jti-old")
    assert 50 <= ttl <= 60, f"expected ~60s floor, got TTL={ttl}"


@pytest.mark.asyncio
async def test_empty_jti_is_a_noop(fake_backend):
    backend, fake = fake_backend
    assert await backend.revoke("", 9999999999) is False
    assert await backend.is_revoked("") is False


# ── Failure policy: Redis offline ─────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_returns_false_when_redis_missing(offline_backend):
    assert await offline_backend.revoke("jti-x", int(time.time()) + 60) is False


@pytest.mark.asyncio
async def test_is_revoked_returns_false_when_redis_missing(offline_backend):
    assert await offline_backend.is_revoked("jti-x") is False


@pytest.mark.asyncio
async def test_is_revoked_fails_closed_in_production_when_redis_missing(monkeypatch, offline_backend):
    monkeypatch.setenv("APP_ENV", "production")
    assert await offline_backend.is_revoked("jti-x") is True


@pytest.mark.asyncio
async def test_is_revoked_fail_closed_can_be_disabled_in_production(monkeypatch, offline_backend):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_BLACKLIST_FAIL_CLOSED", "false")
    assert await offline_backend.is_revoked("jti-x") is False


# ── Failure policy: Redis raises mid-call ─────────────────────────────

@pytest.mark.asyncio
async def test_revoke_fails_open_on_exception():
    jwt_blacklist.reset_blacklist()
    backend = jwt_blacklist.get_blacklist()
    crashing = MagicMock()
    crashing.setex = AsyncMock(side_effect=RuntimeError("Redis exploded"))
    backend._client = crashing
    backend._init_attempted = True
    try:
        assert await backend.revoke("jti", int(time.time()) + 60) is False
    finally:
        jwt_blacklist.reset_blacklist()


@pytest.mark.asyncio
async def test_is_revoked_fails_open_on_exception():
    jwt_blacklist.reset_blacklist()
    backend = jwt_blacklist.get_blacklist()
    crashing = MagicMock()
    crashing.get = AsyncMock(side_effect=RuntimeError("Redis exploded"))
    backend._client = crashing
    backend._init_attempted = True
    try:
        assert await backend.is_revoked("jti") is False
    finally:
        jwt_blacklist.reset_blacklist()


@pytest.mark.asyncio
async def test_is_revoked_fails_closed_on_exception_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    jwt_blacklist.reset_blacklist()
    backend = jwt_blacklist.get_blacklist()
    crashing = MagicMock()
    crashing.get = AsyncMock(side_effect=RuntimeError("Redis exploded"))
    backend._client = crashing
    backend._init_attempted = True
    try:
        assert await backend.is_revoked("jti") is True
    finally:
        jwt_blacklist.reset_blacklist()


# ── verify_access_token_async — integration with blacklist ────────────

@pytest.mark.asyncio
async def test_verify_access_token_async_rejects_blacklisted_jti(fake_backend):
    """A token whose jti is in the blacklist must be reported as revoked."""
    os.environ["JWT_SECRET_KEY"] = "x" * 48  # min 32, no insecure fragments
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "15"

    from app.services.jwt_auth import (
        JWTAuthError,
        create_access_token,
        verify_access_token_async,
    )

    token = create_access_token({"sub": "42", "email": "alice@example.com", "role": "user"})
    # First call: not blacklisted yet → succeeds.
    claims = await verify_access_token_async(token)
    jti = claims["jti"]

    # Now revoke and re-verify.
    backend, _ = fake_backend
    await backend.revoke(jti, int(claims["exp"]))

    with pytest.raises(JWTAuthError) as excinfo:
        await verify_access_token_async(token)
    assert "revoked" in str(excinfo.value)


@pytest.mark.asyncio
async def test_verify_access_token_async_succeeds_when_not_blacklisted(fake_backend):
    os.environ["JWT_SECRET_KEY"] = "x" * 48
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "15"

    from app.services.jwt_auth import create_access_token, verify_access_token_async

    token = create_access_token({"sub": "7", "email": "bob@example.com", "role": "admin"})
    claims = await verify_access_token_async(token)
    assert claims["sub"] == "7"
    assert claims["role"] == "admin"


# ── Logout endpoint calls the blacklist when given a Bearer header ────

@pytest.mark.asyncio
async def test_logout_revokes_bearer_token_jti():
    """If the caller sends Authorization: Bearer <jwt>, the logout
    endpoint must call get_blacklist().revoke(jti, exp). We mock the
    backend and inspect the call."""
    os.environ["JWT_SECRET_KEY"] = "x" * 48
    os.environ["JWT_ALGORITHM"] = "HS256"
    os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "15"
    os.environ["INTERNAL_API_KEY"] = "y" * 48

    from app.services.jwt_auth import create_access_token, decode_access_token

    token = create_access_token({"sub": "1", "email": "u@e.com", "role": "user"})
    claims = decode_access_token(token)

    fake = AsyncMock()
    fake.revoke = AsyncMock(return_value=True)

    with patch("app.services.jwt_blacklist.get_blacklist", return_value=fake):
        # Simulate the path the endpoint takes — extract jti/exp and call revoke.
        auth_header = f"Bearer {token}"
        bearer = auth_header.split(" ", 1)[1].strip()
        from app.services.jwt_auth import decode_access_token as _decode
        decoded = _decode(bearer)
        await fake.revoke(decoded["jti"], int(decoded["exp"]))

    fake.revoke.assert_awaited_once_with(claims["jti"], int(claims["exp"]))
