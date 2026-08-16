"""F-SEG focal guards for Workspace Decision mutations."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture
def workspace_main(monkeypatch):
    _purge_app_modules()
    saved_path = list(sys.path)
    sys.path[:] = [
        value
        for value in sys.path
        if not any(marker in value for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(WORKSPACE_ROOT))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "INTERNAL_API_KEY",
        "workspace_fseg_internal_key_with_more_than_32_chars",
    )
    main = importlib.import_module("app.main")
    yield main
    sys.path[:] = saved_path
    _purge_app_modules()


MUTATING_DECISION_ROUTES = {
    ("POST", "/api/decisions"),
    ("PATCH", "/api/decisions/{decision_id}"),
    ("DELETE", "/api/decisions/{decision_id}"),
    ("POST", "/api/decisions/{decision_id}/actions"),
}


def test_all_decision_mutations_require_control_room_write(workspace_main):
    found: dict[tuple[str, str], bool] = {}
    for route in workspace_main.app.routes:
        for method in getattr(route, "methods", None) or set():
            key = (method, getattr(route, "path", ""))
            if key not in MUTATING_DECISION_ROUTES:
                continue
            found[key] = any(
                getattr(getattr(dep, "call", None), "required_permission", None)
                == "control_room.write"
                for dep in route.dependant.dependencies
            )

    assert set(found) == MUTATING_DECISION_ROUTES
    assert all(found.values())


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/decisions", {"title": "synthetic-canary"}),
        ("PATCH", "/api/decisions/900001", {"title": "synthetic-canary"}),
        ("DELETE", "/api/decisions/900001", None),
        (
            "POST",
            "/api/decisions/900001/actions",
            {"action_text": "synthetic-canary"},
        ),
    ],
)
def test_viewer_gets_403_before_decision_db_access(
    workspace_main, monkeypatch, method, path, body
):
    viewer = {
        "id": 900001,
        "email": "viewer.invalid",
        "role": "viewer",
        "workspace_role": "viewer",
        "active_tenant_id": "00000000-0000-0000-0000-000000000901",
        "active_workspace_id": "00000000-0000-0000-0000-000000000902",
    }

    async def session_user(_token, _requested_workspace_id=None):
        return viewer

    async def forbidden_db():
        raise AssertionError("viewer request reached the database")

    monkeypatch.setattr(workspace_main._session, "get_session_user", session_user)
    monkeypatch.setattr(workspace_main, "pg", forbidden_db)

    csrf = "synthetic-csrf-canary"
    with TestClient(workspace_main.app) as client:
        response = client.request(
            method,
            path,
            json=body,
            cookies={"mod_session": "synthetic-session", "csrf_token": csrf},
            headers={"X-CSRF-Token": csrf},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "permission required: control_room.write"}


@pytest.mark.asyncio
async def test_db_permission_denied_is_translated_to_403(workspace_main, monkeypatch):
    class PermissionDenied(Exception):
        sqlstate = "42501"

    class DeniedPool:
        async def fetchrow(self, _sql, *_params):
            raise PermissionDenied("synthetic database detail must not leak")

    async def pool():
        return DeniedPool()

    monkeypatch.setattr(workspace_main, "pg", pool)
    request = SimpleNamespace(
        state=SimpleNamespace(
            user={
                "id": 900002,
                "role": "workspace_admin",
                "active_workspace_id": "00000000-0000-0000-0000-000000000903",
            }
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_main.api_decisions_create(
            request, {"title": "synthetic-canary"}
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "decision operation forbidden"
    assert "database detail" not in str(exc_info.value.detail)
