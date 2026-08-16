from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INTERNAL_API_KEY", "workspace-test-internal-key-material-000000000000")

from app import main
from app.services import session


@pytest.mark.asyncio
async def test_logout_hashes_both_cookies_and_uses_atomic_auth_boundary() -> None:
    fake_pool = AsyncMock()
    fake_pool.fetchrow.return_value = {
        "session_deleted": True,
        "refresh_revoked": True,
    }

    with patch.object(session, "pool", return_value=fake_pool):
        result = await session.logout_tokens(
            "opaque-cookie-value", "opaque-refresh-value"
        )

    query, session_digest, refresh_digest = fake_pool.fetchrow.await_args.args
    assert query == "SELECT * FROM omega_auth_logout($1, $2)"
    assert session_digest == session.hash_session_token("opaque-cookie-value")
    assert refresh_digest == session.hash_refresh_token("opaque-refresh-value")
    assert "opaque" not in session_digest
    assert "opaque" not in refresh_digest
    assert result == (True, True)


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


@pytest.mark.parametrize(
    "workspace_header",
    (None, "not-a-uuid", "22222222-2222-2222-2222-222222222222"),
)
def test_logout_bypasses_workspace_resolution_but_keeps_csrf(
    workspace_header: str | None,
) -> None:
    logout = AsyncMock(return_value=(True, True))
    should_not_resolve = AsyncMock(
        side_effect=AssertionError("logout must not resolve session/workspace")
    )
    headers = {"X-CSRF-Token": "logout-csrf"}
    if workspace_header is not None:
        headers["X-Workspace-ID"] = workspace_header

    with (
        patch.object(main._session, "get_session_user", should_not_resolve),
        patch.object(main._session, "logout_tokens", logout),
    ):
        response = TestClient(main.app).post(
            "/auth/logout",
            cookies={
                main._session.COOKIE_NAME: "expired-or-forced-change-session",
                main._session.REFRESH_COOKIE_NAME: "refresh-cookie",
                "csrf_token": "logout-csrf",
            },
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json() == {"logged_out": True}
    should_not_resolve.assert_not_awaited()
    logout.assert_awaited_once_with(
        "expired-or-forced-change-session", "refresh-cookie"
    )
    set_cookie = response.headers.get("set-cookie", "")
    for cookie_name in ("mod_session", "refresh_token", "csrf_token"):
        assert f"{cookie_name}=" in set_cookie


def test_logout_is_http_idempotent_after_credentials_are_gone() -> None:
    logout = AsyncMock(return_value=(False, False))
    with patch.object(main._session, "logout_tokens", logout):
        response = TestClient(main.app).post(
            "/auth/logout",
            cookies={"csrf_token": "second-logout-csrf"},
            headers={
                "X-CSRF-Token": "second-logout-csrf",
                "X-Workspace-ID": "malformed-on-purpose",
            },
        )

    assert response.status_code == 200
    logout.assert_awaited_once_with(None, None)


def test_logout_still_rejects_forced_cross_site_request() -> None:
    logout = AsyncMock()
    with patch.object(main._session, "logout_tokens", logout):
        response = TestClient(main.app).post(
            "/auth/logout",
            cookies={
                main._session.COOKIE_NAME: "session-cookie",
                "csrf_token": "real-csrf",
            },
        )

    assert response.status_code == 403
    logout.assert_not_awaited()
