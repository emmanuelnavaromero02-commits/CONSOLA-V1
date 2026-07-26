from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.routers import control_room
from app.services import control_room_service
from app.services.control_room.business_action_authority import (
    action_item_is_current,
)
from control_room_surface_fixtures import OPERATOR, business_item


@pytest.mark.parametrize("status", ("approved", "dismissed", "resolved"))
def test_terminal_statuses_never_allow_preview(status: str):
    assert not action_item_is_current(
        business_item(status=status),
        operation="preview",
    )


@pytest.mark.parametrize(
    ("operation", "status", "expected"),
    (
        ("preview", "open", True),
        ("dry_run", "decision_created", True),
        ("dry_run", "approved", False),
        ("execute", "approved", True),
        ("execute", "open", False),
    ),
)
def test_action_operation_has_explicit_current_states(
    operation: str,
    status: str,
    expected: bool,
):
    assert (
        action_item_is_current(
            business_item(status=status),
            operation=operation,
        )
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    (
        {"data_status": "stale"},
        {"status": "approved"},
        {"execution_status": "executed"},
    ),
)
async def test_preview_replay_rejects_stale_or_terminal_before_db(updates):
    item = business_item(**updates)
    lookup = AsyncMock(return_value=item)
    pool = AsyncMock(side_effect=AssertionError("database reached"))
    with (
        patch.object(control_room_service, "_item_for_mutation", lookup),
        patch.object(control_room_service.auth, "pool", pool),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.action_preview(
                str(item["id"]),
                OPERATOR,
                template_id="request_owner_review",
            )

    assert exc.value.status_code == 409
    pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_replay_rejects_stale_item_before_db():
    item = business_item(data_status="stale", status="approved")
    pool = AsyncMock(side_effect=AssertionError("database reached"))
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(control_room_service.auth, "pool", pool),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                str(item["id"]),
                OPERATOR,
                template_id="request_owner_review",
                confirm_execute=True,
            )

    assert exc.value.status_code == 409
    pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_revalidates_disabled_template_before_dml():
    item = business_item()
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.execute.side_effect = AssertionError("DML reached")

    async def scoped(_pool, _user, work):
        return await work(conn, "tenant-a", "workspace-a")

    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(control_room_service.auth, "pool", new=AsyncMock(return_value={})),
        patch.object(
            control_room_service,
            "_run_with_db_scope",
            new=AsyncMock(side_effect=scoped),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.action_preview(
                str(item["id"]),
                OPERATOR,
                template_id="request_owner_review",
            )

    assert exc.value.status_code == 404
    assert conn.fetchrow.await_count == 1
    assert "FROM control_room_action_templates" in conn.fetchrow.await_args.args[0]
    conn.execute.assert_not_awaited()


def _client(user: dict) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def _current_user():
        return user

    app.dependency_overrides[control_room.require_authenticated] = _current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.parametrize(
    "path",
    (
        "/api/control-room/items/business-1/action-preview",
        "/api/control-room/items/business-1/action-dry-run",
        "/api/control-room/items/business-1/execute",
    ),
)
def test_action_routes_require_explicit_strict_binding(path: str):
    client = _client(OPERATOR)
    with (
        patch.object(control_room_service, "action_preview", new=AsyncMock()),
        patch.object(control_room_service, "action_dry_run", new=AsyncMock()),
        patch.object(control_room_service, "execute_item", new=AsyncMock()),
    ):
        missing = client.post(path, headers={"authorization": "Bearer test"}, json={})
        extra = client.post(
            path,
            headers={"authorization": "Bearer test"},
            json={"template_id": "request_owner_review", "endpoint": "//evil"},
        )

    assert missing.status_code == 422
    assert extra.status_code == 422
