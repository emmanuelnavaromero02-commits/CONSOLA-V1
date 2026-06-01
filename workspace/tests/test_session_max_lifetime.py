"""Workspace sessions must honor the same absolute cap as Console.

Workspace reads the shared ``user_sessions`` table so Console cookies work on
the app surface too. If Workspace only applies the sliding window, a long-lived
or stolen cookie can keep extending here after Console would reject it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import session


def _session_row(**overrides):
    row = {
        "token": "tok-1",
        "expires_at": datetime.now(timezone.utc) + session.SESSION_LIFETIME,
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "id": 42,
        "email": "alice@example.com",
        "name": "Alice",
        "role": "user",
        "is_active": True,
        "must_change_password": False,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_workspace_session_within_absolute_cap_is_returned():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row()
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_memberships", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is not None
    assert user["id"] == 42
    assert user["email"] == "alice@example.com"
    delete_calls = [
        call for call in mock_pool.execute.call_args_list
        if call.args and "DELETE FROM user_sessions" in call.args[0]
    ]
    assert not delete_calls


@pytest.mark.asyncio
async def test_workspace_session_older_than_cap_is_invalidated():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(
        created_at=datetime.now(timezone.utc) - timedelta(hours=13),
    )
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_memberships", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is None
    delete_calls = [
        call for call in mock_pool.execute.call_args_list
        if call.args and "DELETE FROM user_sessions" in call.args[0]
    ]
    assert delete_calls
    assert delete_calls[0].args[1] == "tok-1"


@pytest.mark.asyncio
async def test_workspace_legacy_session_without_created_at_still_works():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(created_at=None)
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_memberships", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is not None


@pytest.mark.asyncio
async def test_workspace_sliding_window_still_extends_under_cap():
    mock_pool = AsyncMock()
    mock_pool.fetchrow.return_value = _session_row(
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_memberships", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is not None
    update_calls = [
        call for call in mock_pool.execute.call_args_list
        if call.args and "UPDATE user_sessions SET expires_at" in call.args[0]
    ]
    assert update_calls


def test_workspace_max_session_lifetime_constant_is_reasonable():
    assert session.MAX_SESSION_LIFETIME <= timedelta(hours=24)
    assert session.MAX_SESSION_LIFETIME > timedelta(minutes=30)
