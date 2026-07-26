from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers import control_room
from app.schemas.control_room_legacy_responses import (
    ControlRoomLegacyActionRunsResponse,
    ControlRoomLegacyActivityResponse,
    ControlRoomLegacyAlertsResponse,
    ControlRoomLegacyAnomaliesResponse,
    ControlRoomLegacyDashboardResponse,
    ControlRoomLegacyImpactResponse,
    ControlRoomLegacyItemResponse,
    ControlRoomLegacyOutcomesResponse,
    public_projection_key_is_forbidden,
    redact_public_control_room_projection,
)
from app.services import control_room_service


OPERATOR = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
ANALYST = {
    "id": 8,
    "role": "user",
    "workspace_role": "analyst",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _client(user: dict | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def current_user():
        return user

    app.dependency_overrides[control_room.require_authenticated] = current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def _forbidden_paths(value, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if public_projection_key_is_forbidden(key):
                paths.append(path)
            paths.extend(_forbidden_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return paths


def _raw_item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "title": "Revisar senal",
        "severity": "high",
        "status": "open",
        "omega": {"step": "observe"},
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "source_dataset": "gold_private",
        "sql": "SELECT secret FROM private",
        "action_templates": [{"template_id": "write-private"}],
        "metadata": {
            "explicit_action_bindings": [{"binding_id": "private"}],
            "connection": {"password": "private"},
        },
        "nested": {
            "payload": {"employee": "private"},
            "provenance": {"source": "private"},
            "receipt": {"digest": "private"},
        },
    }


def test_redactor_is_recursive_non_mutating_and_removes_server_authority():
    raw = {"items": [_raw_item()]}
    before = deepcopy(raw)

    redacted = redact_public_control_room_projection(raw)

    assert raw == before
    assert _forbidden_paths(redacted) == []
    assert redacted["items"][0]["title"] == "Revisar senal"


@pytest.mark.parametrize(
    ("path", "service_name", "payload"),
    (
        (
            "/api/control-room/dashboard",
            "dashboard",
            {
                "meta": {},
                "workspace": {"workspace_id": "workspace-a"},
                "period": "July 2026",
                "omega_steps": [],
                "summary": {},
                "domains": [],
                "cartridges": [],
                "sources": [{"dataset": "gold_private", "status": "ok"}],
                "alerts": [{**_raw_item(), "item_id": "item-1"}],
                "items": [_raw_item()],
            },
        ),
        (
            "/api/control-room/alerts",
            "list_alerts",
            {
                "alerts": [{**_raw_item(), "item_id": "item-1"}],
                "summary": {},
                "generated_at": "2026-07-26T10:00:00Z",
            },
        ),
        ("/api/control-room/items/item-1", "get_item", _raw_item()),
    ),
)
def test_retained_legacy_http_projections_never_expose_raw_authority(
    path: str,
    service_name: str,
    payload: dict,
):
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    service = AsyncMock(return_value=payload)
    with patch.object(control_room_service, service_name, service):
        response = _client(OPERATOR).get(path)

    assert response.status_code == 200
    assert _forbidden_paths(response.json()) == []
    assert "private" not in response.text
    service.assert_awaited_once()


def test_legacy_projection_routes_are_typed_and_operations_only():
    expected = {
        "/api/control-room/dashboard": ControlRoomLegacyDashboardResponse,
        "/api/control-room/alerts": ControlRoomLegacyAlertsResponse,
        "/api/control-room/anomalies": ControlRoomLegacyAnomaliesResponse,
        "/api/control-room/items/{item_id}": ControlRoomLegacyItemResponse,
        "/api/control-room/anomalies/{anomaly_id}": ControlRoomLegacyItemResponse,
        "/api/control-room/items/{item_id}/impact": ControlRoomLegacyImpactResponse,
        "/api/control-room/items/{item_id}/activity": ControlRoomLegacyActivityResponse,
        "/api/control-room/items/{item_id}/action-runs": ControlRoomLegacyActionRunsResponse,
        "/api/control-room/items/{item_id}/outcomes": ControlRoomLegacyOutcomesResponse,
    }
    by_path = {
        route.path: route
        for route in control_room.router.routes
        if "GET" in (route.methods or set())
    }

    for path, response_model in expected.items():
        route = by_path[path]
        permissions = {
            dependency.dependency.required_permission
            for dependency in route.dependencies
            if hasattr(dependency.dependency, "required_permission")
        }
        assert route.response_model is response_model
        assert permissions == {"operations.read"}


@pytest.mark.parametrize(
    ("path", "service_name"),
    (
        ("/api/control-room/dashboard", "dashboard"),
        ("/api/control-room/alerts", "list_alerts"),
        ("/api/control-room/items/item-1", "get_item"),
    ),
)
def test_dataset_reader_cannot_open_legacy_operational_projections(
    path: str, service_name: str
):
    service = AsyncMock(side_effect=AssertionError("service reached"))
    with patch.object(control_room_service, service_name, service):
        response = _client(ANALYST).get(path)

    assert response.status_code == 403
    service.assert_not_awaited()


def test_talent_business_payload_is_typed_read_only_and_redacted():
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    raw = {
        "generated_at": "2026-07-26T10:00:00Z",
        "connection_id": "private",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "dataset": "gold_private",
        "status": "ready",
        "summary": {"total": 1},
        "items": [
            {
                "id": "signal-1",
                "title": "Revisar cohorte",
                "method": "private_algorithm",
                "preview_available": True,
                "metadata": {"payload": "private"},
            }
        ],
        "blockers": [],
    }
    service = AsyncMock(return_value=raw)
    with patch.object(
        control_room_service,
        "sap_successfactors_talent_anomalies",
        service,
    ):
        response = _client(OPERATOR).get(
            "/api/control-room/sap-successfactors/talent/anomalies"
        )

    assert response.status_code == 200
    assert _forbidden_paths(response.json()) == []
    assert response.json()["items"] == [{"id": "signal-1", "title": "Revisar cohorte"}]
    assert "private" not in response.text


@pytest.mark.parametrize(
    "path",
    (
        "/api/control-room/items/item-1/action-preview",
        "/api/control-room/items/item-1/action-dry-run",
        "/api/control-room/items/item-1/execute",
        "/api/control-room/sap-successfactors/talent/actions/preview",
    ),
)
def test_unconsumed_legacy_action_posts_are_gone_without_a_body(path: str):
    services = (
        patch.object(control_room_service, "action_preview", AsyncMock()),
        patch.object(control_room_service, "action_dry_run", AsyncMock()),
        patch.object(control_room_service, "execute_item", AsyncMock()),
        patch.object(
            control_room_service,
            "sap_successfactors_talent_action_preview",
            AsyncMock(),
        ),
    )
    with (
        services[0] as preview,
        services[1] as dry_run,
        services[2] as execute,
        services[3] as talent,
    ):
        response = _client(OPERATOR).post(path, json={"payload": "private"})

    assert response.status_code == 410
    assert response.content == b""
    for service in (preview, dry_run, execute, talent):
        service.assert_not_awaited()


def test_canonical_action_handle_preview_remains_typed_and_active():
    route = next(
        route
        for route in control_room.router.routes
        if route.path == "/api/control-room/actions/preview"
    )
    assert route.status_code != 410
    assert route.response_model is not None
