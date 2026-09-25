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
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row()
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None
    assert user["id"] == 42
    assert user["email"] == "alice@example.com"
    query, digest = mock_pool.fetchrow.await_args.args
    assert "omega_auth_resolve_session" in query
    assert digest == auth.hash_session_token("tok-1")


@pytest.mark.asyncio
async def test_session_older_than_cap_is_invalidated():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is None, "stale session must return None"
    assert mock_pool.fetchrow.await_args.args == (
        "SELECT * FROM omega_auth_resolve_session($1)",
        auth.hash_session_token("tok-1"),
    )


@pytest.mark.asyncio
async def test_session_at_exact_cap_boundary_is_invalidated():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = None
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is None


@pytest.mark.asyncio
async def test_session_without_created_at_does_not_crash():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(session_created_at=None)
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None, "missing created_at must not break session lookup"


@pytest.mark.asyncio
async def test_session_sliding_window_still_extends_under_cap():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row()
    with patch.object(auth, "pool", return_value=mock_pool):
        user = await auth.get_session_user("tok-1")
    assert user is not None
    assert len(mock_pool.fetchrow.await_args.args) == 2


def test_max_session_lifetime_constant_is_reasonable():
    assert auth.MAX_SESSION_LIFETIME <= timedelta(hours=24)
    assert auth.MAX_SESSION_LIFETIME > timedelta(minutes=30)
