from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.domains.iam.access_payload import access_ui_capabilities
from app.routers import pages


PLAIN_USER = {"id": 1, "role": "user"}
ANALYST = {"id": 2, "role": "analyst"}
TENANT_ADMIN = {"id": 3, "role": "user", "workspace_role": "tenant_admin"}
WORKSPACE_ADMIN = {"id": 4, "role": "user", "workspace_role": "workspace_admin"}
PLATFORM_ADMIN = {"id": 5, "role": "admin"}
OWNER = {"id": 6, "role": "owner", "workspace_role": "workspace_admin"}


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


@pytest.mark.parametrize("path", ("/copilot/knowledge", "/copilot/knowledge/"))
@pytest.mark.parametrize("user", (WORKSPACE_ADMIN, PLATFORM_ADMIN, OWNER), ids=("workspace_admin", "admin", "owner"))
def test_knowledge_page_allows_workspace_and_platform_admins(monkeypatch, path, user):
    response = _client(monkeypatch, user).get(path)
    assert response.status_code == 200
    assert response.text == "copilot/knowledge/index.html"


@pytest.mark.parametrize("path", ("/copilot/knowledge", "/copilot/knowledge/"))
@pytest.mark.parametrize("user", (PLAIN_USER, ANALYST, TENANT_ADMIN), ids=("user", "analyst", "tenant_admin"))
def test_knowledge_page_rejects_non_admin_users(monkeypatch, path, user):
    assert _client(monkeypatch, user).get(path).status_code == 403


def test_knowledge_page_requires_authentication(monkeypatch):
    app = FastAPI()

    @app.middleware("http")
    async def _no_user(request: Request, call_next):
        request.state.user = None
        return await call_next(request)

    app.include_router(pages.router)
    client = TestClient(app)
    assert client.get("/copilot/knowledge").status_code == 401


def _rag_write_probe_client(user: Mapping[str, object]) -> TestClient:
    import app.main as console_main
    from app import dependencies as deps

    probe_app = FastAPI()

    @probe_app.post("/probe", dependencies=[Depends(console_main._require_rag_write_role)])
    async def probe():
        return {"ok": True}

    probe_app.dependency_overrides[deps.get_current_user] = lambda: dict(user)
    return TestClient(probe_app)


@pytest.mark.parametrize(
    "user",
    (
        {"id": 1, "role": "owner"},
        {"id": 2, "role": "super_admin"},
        {"id": 3, "role": "admin"},
        {"id": 4, "role": "user", "workspace_role": "workspace_admin"},
    ),
    ids=("owner", "super_admin", "admin", "workspace_admin"),
)
def test_rag_write_guard_accepts_platform_and_workspace_admins(user):
    assert _rag_write_probe_client(user).post("/probe").status_code == 200


@pytest.mark.parametrize(
    "user",
    (
        {"id": 5, "role": "user"},
        {"id": 6, "role": "user", "workspace_role": "tenant_admin"},
        {"id": 7, "role": "analyst"},
        {"id": 8, "role": "viewer"},
    ),
    ids=("user", "tenant_admin", "analyst", "viewer"),
)
def test_rag_write_guard_rejects_non_admin_roles(user):
    assert _rag_write_probe_client(user).post("/probe").status_code == 403


def test_knowledge_capability_matches_page_guard():
    workspace_admin = access_ui_capabilities(
        {"datasets.write"},
        role_canonical="user",
        workspace_role_resolved="workspace_admin",
    )
    tenant_admin = access_ui_capabilities(
        {"datasets.read"},
        role_canonical="user",
        workspace_role_resolved="tenant_admin",
    )
    platform_admin = access_ui_capabilities(
        {"datasets.write"},
        role_canonical="admin",
        workspace_role_resolved="workspace_admin",
    )
    plain_user = access_ui_capabilities(
        {"workspace.access"},
        role_canonical="user",
        workspace_role_resolved=None,
    )

    assert workspace_admin["can_view_knowledge"] is True
    assert platform_admin["can_view_knowledge"] is True
    assert tenant_admin["can_view_knowledge"] is False
    assert plain_user["can_view_knowledge"] is False
