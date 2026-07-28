from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import control_room as routes
from app.schemas.control_room_action_requests import ControlRoomActionHandleRequest
from app.schemas.control_room_experience_actions import (
    ExperienceActionPreviewResponse,
)
from app.services import control_room_service
from app.services.control_room import business_action_handle
from app.services.control_room.business_action_handle import (
    ResolvedActionHandle,
    resolve_business_action_handle,
)
from control_room_surface_fixtures import OPERATOR, action_item, snapshot


def _handle(item: dict) -> str:
    return str(item["metadata"]["explicit_action_bindings"][0]["binding_id"])


@pytest.mark.asyncio
async def test_handle_resolves_only_from_current_scoped_signed_binding():
    item = action_item()
    with (
        patch.object(
            business_action_handle,
            "collect_surface_snapshot",
            AsyncMock(return_value=snapshot(items=(item,))),
        ),
        patch.object(
            business_action_handle,
            "load_enabled_action_template_ids",
            AsyncMock(return_value=frozenset({"request_owner_review"})),
        ),
    ):
        result = await resolve_business_action_handle(OPERATOR, _handle(item))

    assert result == ResolvedActionHandle(
        item_id="business-1",
        template_id="request_owner_review",
        binding_id=_handle(item),
    )


@pytest.mark.asyncio
async def test_cross_workspace_handle_is_404_without_action_metadata():
    item = action_item()
    foreign = {**OPERATOR, "active_workspace_id": "foreign-workspace"}
    with (
        patch.object(
            business_action_handle,
            "collect_surface_snapshot",
            AsyncMock(return_value=snapshot(items=(item,))),
        ),
        patch.object(
            business_action_handle,
            "load_enabled_action_template_ids",
            AsyncMock(return_value=frozenset({"request_owner_review"})),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await resolve_business_action_handle(foreign, _handle(item))

    assert exc.value.status_code == 404
    assert exc.value.detail == "action binding not found"


@pytest.mark.asyncio
async def test_public_preview_expands_handle_server_side_only():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/control-room/actions/preview",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )
    resolved = ResolvedActionHandle(
        item_id="business-1",
        template_id="request_owner_review",
        binding_id="a" * 64,
    )
    preview = AsyncMock(
        return_value={
            "execution": {"id": 7},
            "action_run": {"tenant_id": "tenant-a"},
            "payload": {"template_id": "request_owner_review", "sql": "secret"},
            "item": {"id": "business-1", "workspace_id": "workspace-a"},
        }
    )
    with (
        patch.object(
            routes, "resolve_business_action_handle", AsyncMock(return_value=resolved)
        ),
        patch.object(control_room_service, "action_preview", preview),
    ):
        response = await routes.control_room_action_handle_preview(
            request,
            ControlRoomActionHandleRequest(action_handle="a" * 64),
            OPERATOR,
        )

    assert response.model_dump() == {
        "action_handle": "a" * 64,
        "operation": "preview",
        "status": "generated",
        "message": "Preview generado; no se ejecuto ningun cambio externo.",
    }
    preview.assert_awaited_once_with(
        "business-1",
        OPERATOR,
        template_id="request_owner_review",
        binding_id="a" * 64,
        ip="127.0.0.1",
        user_agent=None,
    )


def test_public_preview_route_is_post_write_scoped():
    route = next(
        value
        for value in routes.router.routes
        if value.path == "/api/control-room/actions/preview"
    )
    permissions = {
        dependency.dependency.required_permission
        for dependency in route.dependencies
        if hasattr(dependency.dependency, "required_permission")
    }

    assert route.methods == {"POST"}
    assert permissions == {"control_room.write"}
    assert route.response_model is ExperienceActionPreviewResponse
