from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INTERNAL_API_KEY", "workspace-test-internal-key-material-000000000000")

from app import main
from app.services import session


@pytest.mark.asyncio
async def test_destroy_session_hashes_cookie_and_uses_auth_boundary() -> None:
    fake_pool = AsyncMock()
    fake_pool.fetchval.return_value = True

    with patch.object(session, "pool", return_value=fake_pool):
        await session.destroy_session("opaque-cookie-value")

    query, digest = fake_pool.fetchval.await_args.args
    assert query == "SELECT omega_auth_destroy_session($1)"
    assert digest == session.hash_session_token("opaque-cookie-value")
    assert digest != "opaque-cookie-value"


@pytest.mark.asyncio
async def test_destroy_missing_session_is_idempotent_not_an_error() -> None:
    fake_pool = AsyncMock()
    fake_pool.fetchval.return_value = False

    with patch.object(session, "pool", return_value=fake_pool):
        await session.destroy_session("already-revoked-cookie")

    fake_pool.fetchval.assert_awaited_once()


def test_non_member_workspace_request_is_fail_closed_as_403() -> None:
    denied = AsyncMock(side_effect=PermissionError("workspace access forbidden"))
    with patch.object(main._session, "get_session_user", denied):
        response = TestClient(main.app).get(
            "/auth/me",
            cookies={main._session.COOKIE_NAME: "opaque-cookie"},
            headers={"x-workspace-id": "22222222-2222-2222-2222-222222222222"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "workspace access forbidden"}
