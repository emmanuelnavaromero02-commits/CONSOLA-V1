"""Sprint v1.4 FIX 1 — Absolute session lifetime cap.

Sliding sessions can be extended indefinitely while a user is active.
That defeats password / role revocation for long-running sessions and
gives a stolen cookie infinite lifetime. `get_session_user` now also
checks `created_at`: any session older than MAX_SESSION_LIFETIME is
destroyed server-side and the caller must log back in.

Patterns mirror the rest of console/tests/* — patch `auth.pool` with a
return_value=AsyncMock() so `await auth.pool()` yields the mock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import auth


def _session_row(**overrides):
    row = {
        "user_id":              42,
        "session_expires_at":   datetime.now(timezone.utc) + auth.SESSION_LIFETIME,
        "session_created_at":   datetime.now(timezone.utc) - timedelta(minutes=5),
        "email":                "alice@example.com",
        "name":                 "Alice",
        "role":                 "user",
        "is_active":            True,
        "must_change_password": False,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_session_within_absolute_cap_is_returned():
    """A session created 5 minutes ago survives — well inside MAX_SESSION_LIFETIME."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row()
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None
    assert user["id"] == 42
    assert user["email"] == "alice@example.com"
    query, digest, _new_expiry, _slide_before, _created_after = mock_pool.fetchrow.await_args.args
    assert "omega_auth_resolve_session" in query
    assert digest == auth.hash_session_token("tok-1")


@pytest.mark.asyncio
async def test_session_older_than_cap_is_invalidated():
    """Created 13h ago — exceeds MAX_SESSION_LIFETIME (12h) → return None + DELETE row."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is None, "stale session must return None"
    args = mock_pool.fetchrow.await_args.args
    assert args[4] <= datetime.now(timezone.utc) - auth.MAX_SESSION_LIFETIME


@pytest.mark.asyncio
async def test_session_at_exact_cap_boundary_is_invalidated():
    """At MAX_SESSION_LIFETIME + 1ms the cap fires (strict >)."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is None


@pytest.mark.asyncio
async def test_session_without_created_at_does_not_crash():
    """Legacy rows pre-migration have created_at = NULL — fall back to sliding-only."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(session_created_at=None)
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None, "missing created_at must not break session lookup"


@pytest.mark.asyncio
async def test_session_sliding_window_still_extends_under_cap():
    """A session close to expires_at gets pushed forward as before — the cap
    does not interfere with the sliding window when both are satisfied."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row()
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None
    args = mock_pool.fetchrow.await_args.args
    assert args[2] - args[3] == auth.SESSION_SLIDE


def test_max_session_lifetime_constant_is_reasonable():
    """Tampering safeguard: the cap must not be silently raised to days."""
    assert auth.MAX_SESSION_LIFETIME <= timedelta(hours=24)
    assert auth.MAX_SESSION_LIFETIME > timedelta(minutes=30)
