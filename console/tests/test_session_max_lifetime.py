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
        "token":                "tok-1",
        "user_id":              42,
        "expires_at":           datetime.now(timezone.utc) + auth.SESSION_LIFETIME,
        "created_at":           datetime.now(timezone.utc) - timedelta(minutes=5),
        "id":                   42,
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
    # No DELETE was issued.
    delete_calls = [c for c in mock_pool.execute.call_args_list if "DELETE" in (c.args[0] if c.args else "")]
    assert not delete_calls, f"unexpected DELETE: {delete_calls!r}"


@pytest.mark.asyncio
async def test_session_older_than_cap_is_invalidated():
    """Created 13h ago — exceeds MAX_SESSION_LIFETIME (12h) → return None + DELETE row."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(
        created_at=datetime.now(timezone.utc) - timedelta(hours=13),
    )
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is None, "stale session must return None"
    # The session row must have been deleted server-side.
    delete_calls = [c for c in mock_pool.execute.call_args_list
                    if c.args and "DELETE FROM user_sessions" in c.args[0]]
    assert delete_calls, "expected DELETE FROM user_sessions for the stale token"
    # And the token argument must match.
    assert delete_calls[0].args[1] == "tok-1"


@pytest.mark.asyncio
async def test_session_at_exact_cap_boundary_is_invalidated():
    """At MAX_SESSION_LIFETIME + 1ms the cap fires (strict >)."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(
        created_at=datetime.now(timezone.utc)
                   - auth.MAX_SESSION_LIFETIME
                   - timedelta(milliseconds=1),
    )
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is None


@pytest.mark.asyncio
async def test_session_without_created_at_does_not_crash():
    """Legacy rows pre-migration have created_at = NULL — fall back to sliding-only."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(created_at=None)
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None, "missing created_at must not break session lookup"


@pytest.mark.asyncio
async def test_session_sliding_window_still_extends_under_cap():
    """A session close to expires_at gets pushed forward as before — the cap
    does not interfere with the sliding window when both are satisfied."""
    mock_pool = AsyncMock()
    # expires_at very close to NOW → triggers slide branch
    near_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_pool.fetchrow.return_value = _session_row(expires_at=near_expiry)
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None
    # UPDATE expires_at was issued.
    update_calls = [c for c in mock_pool.execute.call_args_list
                    if c.args and "UPDATE user_sessions SET expires_at" in c.args[0]]
    assert update_calls, "sliding window UPDATE was expected"


def test_max_session_lifetime_constant_is_reasonable():
    """Tampering safeguard: the cap must not be silently raised to days."""
    assert auth.MAX_SESSION_LIFETIME <= timedelta(hours=24)
    assert auth.MAX_SESSION_LIFETIME > timedelta(minutes=30)
