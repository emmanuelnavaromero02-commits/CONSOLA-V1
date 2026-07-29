# fmt: off

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.routers import control_room
from app.services import control_room_service
from app.services import permissions


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def read_console_routes() -> str:
    parts = [read("console/app/main.py")]
    routers_dir = ROOT / "console/app/routers/v1"
    parts.extend(
        p.read_text(encoding="utf-8").replace("@router.", "@app.")
        for p in sorted(routers_dir.glob("*.py"))
        if p.name != "__init__.py"
    )
    return "\n".join(parts)


def test_control_room_mutations_use_specific_write_permission():
    router = read("console/app/routers/control_room.py")

    assert '"control_room.write"' in router
    assert 'require_permission("workspace.access")' not in router
    for route in (
        '"/items/{item_id}/step"',
        '"/items/{item_id}/outcomes"',
        '"/items/{item_id}/lessons"',
        '"/items/{item_id}/action-runs"',
        '"/thresholds"',
    ):
        assert route in router
    for route in (
        '"/items/{item_id}/action-preview"',
        '"/items/{item_id}/action-dry-run"',
        '"/items/{item_id}/execute"',
    ):
        route_contract = router.split(route, 1)[1].split(")\nasync def", 1)[0]
        assert "status_code=410" in route_contract
        assert "response_class=Response" in route_contract


def test_control_room_permission_is_registered_and_workspace_admin_can_operate():
    assert "control_room.write" in permissions.PERMISSION_KEYS
    assert "control_room.execute" in permissions.PERMISSION_KEYS
    workspace_admin = permissions.ROLE_PERMISSIONS["workspace_admin"]
    assert "control_room.write" in workspace_admin
    assert "control_room.execute" in workspace_admin


def test_control_room_approve_is_only_in_dedicated_checker_role():
    assert "control_room.approve" in permissions.PERMISSION_KEYS
    for role in (
        "owner", "super_admin", "admin", "workspace_admin", "tenant_admin"
    ):
        assert "control_room.approve" not in permissions.ROLE_PERMISSIONS[role]
    checker = permissions.ROLE_PERMISSIONS["control_room_approver"]
    assert {"control_room.approve", "control_room.execute"} <= checker
    assert "control_room.write" not in checker


def _build_execute_permission_client(user: dict | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    @app.post(
        "/api/control-room/items/{item_id}/execute",
        dependencies=[
            Depends(permissions.require_permission("control_room.write")),
            Depends(permissions.require_permission("control_room.execute")),
        ],
    )
    async def _execute(item_id: str):
        return {"ok": True, "item_id": item_id}

    return TestClient(app, raise_server_exceptions=True)


def test_control_room_execute_permission_runtime_allows_workspace_admin():
    user = {
        "id": 4,
        "email": "ws-admin@example.com",
        "role": "user",
        "workspace_role": "workspace_admin",
    }
    assert permissions.has_permission(user, "control_room.execute")
    response = _build_execute_permission_client(user).post("/api/control-room/items/item-1/execute")
    assert response.status_code == 200


def test_control_room_execute_permission_runtime_rejects_analyst_viewer_and_anonymous():
    for user in (
        {"id": 5, "email": "analyst@example.com", "role": "user", "workspace_role": "analyst"},
        {"id": 6, "email": "viewer@example.com", "role": "viewer"},
    ):
        assert not permissions.has_permission(user, "control_room.execute")
        response = _build_execute_permission_client(user).post("/api/control-room/items/item-1/execute")
        assert response.status_code == 403

    response = _build_execute_permission_client(None).post("/api/control-room/items/item-1/execute")
    assert response.status_code == 401


def _build_real_control_room_router_client(user: dict | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def _current_user():
        if not user:
            return None
        return user

    app.dependency_overrides[control_room.require_authenticated] = _current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def test_real_control_room_execute_route_is_gone_for_legacy_callers():
    for user in (
        {"id": 5, "email": "analyst@example.com", "role": "user", "workspace_role": "analyst"},
        {"id": 6, "email": "viewer@example.com", "role": "viewer"},
        {"id": 4, "email": "admin@example.com", "role": "super_admin"},
    ):
        with patch.object(control_room_service, "execute_item", new=AsyncMock()) as execute_item:
            response = _build_real_control_room_router_client(user).post(
                "/api/control-room/items/item-1/execute",
                headers={"authorization": "Bearer test"},
                json={"template_id": "create_followup_task", "confirm_execute": True},
            )
        assert response.status_code == 410
        assert response.content == b""
        execute_item.assert_not_awaited()


def test_write_permission_returns_403_before_diagnostic_lookup():
    analyst = {
        "id": 5,
        "email": "analyst@example.com",
        "role": "user",
        "workspace_role": "analyst",
    }
    with patch.object(
        control_room_service,
        "create_decision_for_item",
        new=AsyncMock(),
    ) as create_decision:
        response = _build_real_control_room_router_client(analyst).post(
            "/api/control-room/items/source-state-1/decision",
            headers={"authorization": "Bearer test"},
        )

    assert response.status_code == 403
    create_decision.assert_not_awaited()


def test_retired_execute_route_never_reaches_service_for_cookie_session():
    user = {
        "id": 4,
        "email": "ws-admin@example.com",
        "role": "user",
        "workspace_role": "workspace_admin",
    }
    with patch.object(control_room_service, "execute_item", new=AsyncMock()) as execute_item:
        response = _build_real_control_room_router_client(user).post(
            "/api/control-room/items/item-1/execute",
            json={"template_id": "create_followup_task", "confirm_execute": True},
        )
    assert response.status_code == 410
    assert response.content == b""
    execute_item.assert_not_awaited()


def test_control_room_routes_are_workspace_context_enriched():
    main = read("console/app/main.py")

    assert '"/api/control-room"' in main


def test_published_apps_stay_on_console_origin_via_workspace_proxy():
    main = read_console_routes()

    assert '@app.get("/apps/{name}/content"' in main
    assert "_proxy_workspace_app" in main
    assert "RedirectResponse(f\"{workspace_url}/apps" not in main
