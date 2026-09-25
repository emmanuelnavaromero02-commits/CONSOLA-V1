from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def copilot_router():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.routers import copilot as mod
    return mod


def _make_app(mod, *, user=None, bypass_csrf=True):
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.dependencies import require_authenticated
    from app.services.csrf import require_csrf

    effective_user = user or {
        "id": 7,
        "email": "u@example.com",
        "role": "admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
    }

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = effective_user
            return await call_next(request)

    api = FastAPI()
    api.add_middleware(_InjectUser)
    api.include_router(mod.router)

    api.dependency_overrides[require_authenticated] = lambda: effective_user
    if bypass_csrf:
        api.dependency_overrides[require_csrf] = lambda: None
    return api


def test_create_conversation_requires_auth(copilot_router):
    api = FastAPI()
    api.include_router(copilot_router.router)
    r = TestClient(api).post("/api/copilot/conversations", json={"title": "x"})
    assert r.status_code in (401, 403)


def test_list_conversations_requires_auth(copilot_router):
    api = FastAPI()
    api.include_router(copilot_router.router)
    r = TestClient(api).get("/api/copilot/conversations")
    assert r.status_code in (401, 403)


def test_create_conversation_happy(copilot_router):
    api = _make_app(copilot_router)
    seen = {}

    async def fake_create(**kw):
        seen.update(kw)
        return {"id": "abc", "title": "hi"}

    copilot_router.copilot_service.create_conversation = AsyncMock(
        side_effect=fake_create
    )
    r = TestClient(api).post(
        "/api/copilot/conversations", json={"title": "hi"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"id": "abc", "title": "hi"}
    assert seen["user"]["id"] == 7
    assert seen["user"]["active_workspace_id"] == "22222222-2222-2222-2222-222222222222"


def test_list_conversations_filters_by_user(copilot_router):
    api = _make_app(copilot_router)
    seen = {}

    async def fake_list(**kw):
        seen.update(kw)
        return {"conversations": []}

    copilot_router.copilot_service.list_conversations = fake_list
    r = TestClient(api).get("/api/copilot/conversations")
    assert r.status_code == 200
    assert seen["user"]["id"] == 7
    assert seen["user"]["active_workspace_id"] == "22222222-2222-2222-2222-222222222222"


def test_send_message_requires_csrf(copilot_router):
    from app.dependencies import require_authenticated

    api = FastAPI()
    api.include_router(copilot_router.router)
    api.dependency_overrides[require_authenticated] = lambda: {
        "id": 7, "email": "u@example.com", "role": "admin",
    }
    invoked = []
    async def fake_run_turn(**kw):
        invoked.append(kw)
        return {"reply": "ok"}
    copilot_router.copilot_service.run_turn = fake_run_turn

    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages",
        json={"message": "hola"},
    )
    assert r.status_code in (401, 403)
    assert invoked == []


def test_send_message_passes_ip_and_user_agent(copilot_router):
    api = _make_app(copilot_router)
    captured = {}

    async def fake_run_turn(**kw):
        captured.update(kw)
        return {"reply": "ok"}

    copilot_router.copilot_service.run_turn = fake_run_turn
    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages",
        json={"message": "hola"},
        headers={"User-Agent": "test-agent/1.0"},
    )
    assert r.status_code == 200, r.text
    assert captured["conversation_id"] == "c1"
    assert captured["user_message"] == "hola"
    assert captured["user"]["id"] == 7
    assert captured["user_agent"] == "test-agent/1.0"
    assert captured["ip"] is not None


def test_send_empty_message_returns_400(copilot_router):
    api = _make_app(copilot_router)
    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages", json={"message": "   "},
    )
    assert r.status_code == 400


def test_chat_stream_probe_returns_sse(copilot_router):
    api = _make_app(copilot_router)
    r = TestClient(api).get(
        "/api/copilot/chat/00000000-0000-0000-0000-000000000000/stream",
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: ready" in r.text


def test_stream_message_requires_csrf(copilot_router):
    from app.dependencies import require_authenticated

    api = FastAPI()
    api.include_router(copilot_router.router)
    api.dependency_overrides[require_authenticated] = lambda: {
        "id": 7, "email": "u@example.com", "role": "admin",
    }
    invoked = []

    async def fake_open_turn_stream(**kw):
        invoked.append(kw)

    copilot_router.copilot_service.open_turn_stream = fake_open_turn_stream
    r = TestClient(api).post(
        "/api/copilot/chat/00000000-0000-0000-0000-000000000000/stream",
        json={"message": "hola"},
    )
    assert r.status_code in (401, 403)
    assert invoked == []


def test_stream_message_happy_path_emits_sse_and_audits(copilot_router):
    api = _make_app(copilot_router)
    captured = {}
    audit_calls = []

    async def fake_open_turn_stream(**kw):
        captured.update(kw)

        async def events():
            yield {"type": "ready", "conversation_id": kw["conversation_id"]}
            yield {"type": "text_delta", "text": "Ho"}
            yield {
                "type": "done",
                "result": {
                    "message_id": "m1",
                    "reply": "Hola",
                    "tool_calls": [],
                    "tool_results": [],
                    "citations": [],
                    "pending_actions": [],
                    "requires_approval": False,
                },
            }

        return events()

    async def fake_audit(**kw):
        audit_calls.append(kw)

    copilot_router.copilot_service.open_turn_stream = fake_open_turn_stream
    copilot_router.audit_service.record_event = fake_audit
    r = TestClient(api).post(
        "/api/copilot/chat/00000000-0000-0000-0000-000000000000/stream",
        json={"message": "hola"},
        headers={"User-Agent": "test-agent/1.0"},
    )
    assert r.status_code == 200, r.text
    assert "event: token" in r.text
    assert "event: done" in r.text
    assert captured["conversation_id"] == "00000000-0000-0000-0000-000000000000"
    assert captured["user_message"] == "hola"
    assert captured["user"]["id"] == 7
    assert captured["user_agent"] == "test-agent/1.0"
    assert captured["ip"] is not None
    assert audit_calls[0]["action"] == "copilot.message.send"
    assert audit_calls[0]["metadata"]["stream"] is True


def test_get_conversation_returns_only_owner_messages(copilot_router):
    api = _make_app(copilot_router, user={"id": 1, "role": "admin"})
    async def fake_get(*, conversation_id, user):
        assert user["id"] == 1
        return {"messages": []}
    copilot_router.copilot_service.get_conversation_messages = fake_get
    r = TestClient(api).get("/api/copilot/conversations/abc")
    assert r.status_code == 200


def test_approve_action_requires_csrf(copilot_router):
    from app.dependencies import require_authenticated
    api = FastAPI()
    api.include_router(copilot_router.router)
    api.dependency_overrides[require_authenticated] = lambda: {
        "id": 7, "email": "u@example.com", "role": "admin",
    }
    invoked = []
    async def fake_approve(**kw):
        invoked.append(kw)
        return {"reply": "done"}
    copilot_router.copilot_service.approve_pending_action = fake_approve
    r = TestClient(api).post("/api/copilot/conversations/c1/approve/m1")
    assert r.status_code in (401, 403)
    assert invoked == []


def test_approve_action_happy_path(copilot_router):
    api = _make_app(copilot_router)
    captured = {}
    async def fake_approve(**kw):
        captured.update(kw)
        return {"reply": "executed", "requires_approval": False}
    copilot_router.copilot_service.approve_pending_action = fake_approve
    r = TestClient(api).post("/api/copilot/conversations/c1/approve/m1")
    assert r.status_code == 200
    assert captured == {
        "conversation_id": "c1", "message_id": "m1",
        "user": {
            "id": 7,
            "email": "u@example.com",
            "role": "admin",
            "active_tenant_id": "11111111-1111-1111-1111-111111111111",
            "active_workspace_id": "22222222-2222-2222-2222-222222222222",
        },
        "ip": captured["ip"], "user_agent": captured["user_agent"],
    }


def test_router_registered_in_main(copilot_router):
    main_src = (Path(__file__).resolve().parents[1]
                / "console" / "app" / "main.py").read_text(encoding="utf-8")
    assert "copilot_router" in main_src
    assert "app.include_router(copilot_router.router)" in main_src
