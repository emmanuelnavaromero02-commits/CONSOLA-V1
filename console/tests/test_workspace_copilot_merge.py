from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.routers import pages
from app.services.permission_roles import ROLE_PERMISSIONS


REPO = Path(__file__).resolve().parents[2]

WORKSPACE_USER = {"id": 2, "role": "workspace_user"}
LEGACY_USER = {"id": 3, "role": "user"}
APPROVER = {"id": 4, "role": "user", "workspace_role": "control_room_approver"}
AUDITOR = {"id": 5, "role": "auditor"}


def _client(monkeypatch: pytest.MonkeyPatch, user: Mapping[str, object] | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        if user is not None:
            request.state.user = dict(user)
        return await call_next(request)

    monkeypatch.setattr(
        pages,
        "_console_next_response",
        lambda request, path="index.html", **_kw: PlainTextResponse(path),
    )
    app.include_router(pages.router)
    return TestClient(app)


def test_workspace_redirects_303_to_copilot(monkeypatch):
    response = _client(monkeypatch, WORKSPACE_USER).get(
        "/workspace", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/copilot"


def test_workspace_redirect_preserves_prompt(monkeypatch):
    response = _client(monkeypatch, WORKSPACE_USER).get(
        "/workspace",
        params={"prompt": "dame el briefing de hoy"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/copilot?prompt=dame+el+briefing+de+hoy"


def test_workspace_redirect_keeps_workspace_access_guard(monkeypatch):
    assert _client(monkeypatch, AUDITOR).get(
        "/workspace", follow_redirects=False
    ).status_code == 403
    assert _client(monkeypatch, None).get(
        "/workspace", follow_redirects=False
    ).status_code == 401


@pytest.mark.parametrize("user", [WORKSPACE_USER, LEGACY_USER, APPROVER])
def test_merged_roles_reach_copilot_page(monkeypatch, user):
    response = _client(monkeypatch, user).get("/copilot")
    assert response.status_code == 200
    assert response.text == "copilot/index.html"


def test_role_grant_is_read_only_copilot_access():
    for role in ("workspace_user", "user", "control_room_approver"):
        granted = ROLE_PERMISSIONS[role]
        assert "copilot.use" in granted
        assert "copilot.write" not in granted
        assert "copilot.execute" not in granted


def test_workspace_chat_proxies_are_removed(monkeypatch):
    client = _client(monkeypatch, WORKSPACE_USER)
    for path in (
        "/workspace/chat",
        "/workspace/chat/refresh-context",
        "/workspace/chat/stream",
    ):
        assert client.post(path).status_code == 404

    source = (REPO / "console/app/routers/pages.py").read_text(encoding="utf-8")
    assert "workspace/chat" not in source
    assert "WORKSPACE_INTERNAL_URL" not in source


def test_legacy_workspace_static_files_are_gone():
    static = REPO / "console/app/static"
    assert not (static / "workspace.html").exists()
    assert not (static / "js" / "workspace.js").exists()
