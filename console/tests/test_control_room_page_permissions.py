from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.domains.iam.access_payload import access_ui_capabilities
from app.routers import pages


VIEWER = {"id": 1, "role": "viewer"}
WORKSPACE_USER = {"id": 2, "role": "workspace_user"}
WORKSPACE_ADMIN = {"id": 3, "role": "user", "workspace_role": "workspace_admin"}
TENANT_ADMIN = {"id": 4, "role": "user", "workspace_role": "tenant_admin"}


def _client(monkeypatch: pytest.MonkeyPatch, user: Mapping[str, object]) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = dict(user)
        return await call_next(request)

    monkeypatch.setattr(
        pages,
        "_console_next_response",
        lambda request, path="index.html": PlainTextResponse(path),
    )
    app.include_router(pages.router)
    return TestClient(app)


@pytest.mark.parametrize("path", ("/control-room", "/control-room/"))
def test_business_experience_page_requires_datasets_read(monkeypatch, path):
    assert _client(monkeypatch, WORKSPACE_USER).get(path).status_code == 403

    response = _client(monkeypatch, VIEWER).get(path)
    assert response.status_code == 200
    assert response.text == "control-room/index.html"


@pytest.mark.parametrize("path", ("/control-room/talent", "/control-room/talent/"))
def test_talent_page_requires_operations_and_datasets_read(monkeypatch, path):
    assert _client(monkeypatch, VIEWER).get(path).status_code == 403
    assert _client(monkeypatch, WORKSPACE_ADMIN).get(path).status_code == 403

    response = _client(monkeypatch, TENANT_ADMIN).get(path)
    assert response.status_code == 200
    assert response.text == "control-room/talent/index.html"


def test_control_room_navigation_capability_matches_page_permission():
    workspace_only = access_ui_capabilities(
        {"workspace.access"},
        role_canonical="workspace_user",
        workspace_role_resolved=None,
    )
    dataset_reader = access_ui_capabilities(
        {"workspace.access", "datasets.read"},
        role_canonical="viewer",
        workspace_role_resolved=None,
    )

    assert workspace_only["can_view_control_room"] is False
    assert dataset_reader["can_view_control_room"] is True
