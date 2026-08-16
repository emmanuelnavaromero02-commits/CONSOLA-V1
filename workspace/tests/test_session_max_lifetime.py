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
        "session_expires_at": datetime.now(timezone.utc) + session.SESSION_LIFETIME,
        "session_created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "user_id": 42,
        "email": "alice@example.com",
        "name": "Alice",
        "role": "user",
        "is_active": True,
        "must_change_password": False,
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "workspace_name": "Main",
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "tenant_name": "Tenant",
        "workspace_role": "viewer",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_workspace_session_within_absolute_cap_is_returned():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [_session_row()]
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_cartridges", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is not None
    assert user["id"] == 42
    assert user["email"] == "alice@example.com"
    query, digest, _workspace, _new_expiry, _slide_before, _created_after = mock_pool.fetch.await_args.args
    assert "omega_auth_resolve_workspace_session" in query
    assert digest == session.hash_session_token("tok-1")


@pytest.mark.asyncio
async def test_workspace_session_older_than_cap_is_invalidated():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    with (
        patch.object(session, "pool", return_value=mock_pool),
    ):
        user = await session.get_session_user("tok-1")

    assert user is None
    assert mock_pool.fetch.await_args.args[5] <= datetime.now(timezone.utc) - session.MAX_SESSION_LIFETIME


@pytest.mark.asyncio
async def test_workspace_legacy_session_without_created_at_still_works():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [_session_row(session_created_at=None)]
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_cartridges", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is not None


@pytest.mark.asyncio
async def test_workspace_sliding_window_still_extends_under_cap():
    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = [_session_row()]
    with (
        patch.object(session, "pool", return_value=mock_pool),
        patch.object(session, "_workspace_cartridges", new=AsyncMock(return_value=[])),
    ):
        user = await session.get_session_user("tok-1")

    assert user is not None
    args = mock_pool.fetch.await_args.args
    assert args[3] - args[4] == session.SESSION_SLIDE


def test_workspace_max_session_lifetime_constant_is_reasonable():
    assert session.MAX_SESSION_LIFETIME <= timedelta(hours=24)
    assert session.MAX_SESSION_LIFETIME > timedelta(minutes=30)
