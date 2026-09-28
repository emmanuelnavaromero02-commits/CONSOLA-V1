from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_copilot_service import (
    FakeDB,
    _patch_audit,
    _patch_llm,
    _patch_manifest,
    _patch_pool,
)


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


def _fresh_app_modules():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture
def copilot_module():
    _fresh_app_modules()
    from app.services import copilot_service as mod
    return mod


@pytest.fixture
def copilot_router():
    _fresh_app_modules()
    from app.routers import copilot as mod
    return mod


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def admin_user():
    return {
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _capture_chat(seen: dict):
    async def fake_chat(*, system, messages, **_kw):
        # First call is the turn itself; later calls are fact extraction.
        seen.setdefault("system", system)
        text = "ok"
        return (text, [], list(messages) + [{
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }])
    return fake_chat


def test_page_context_is_rendered_into_prompt_and_sanitized(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    seen: dict = {}
    _patch_llm(copilot_module, _capture_chat(seen))

    conv = _run(copilot_module.create_conversation(user=admin_user))
    long_value = "x" * 1200
    _run(copilot_module.run_turn(
        conversation_id=conv["id"],
        user_message="¿qué veo?",
        user=admin_user,
        page_context={
            "route": "/decisions",
            "title": "Decisiones",
            "api_key": "sk-" + "a" * 24,
            "note": long_value,
        },
    ))

    system = seen["system"]
    assert "<USER_PAGE_CONTEXT" in system
    assert '<field name="route">/decisions</field>' in system
    assert "api_key" not in system
    assert "sk-" + "a" * 24 not in system
    assert "x" * 797 + "..." in system
    assert long_value not in system


def test_page_context_is_never_persisted(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    seen: dict = {}
    _patch_llm(copilot_module, _capture_chat(seen))

    conv = _run(copilot_module.create_conversation(user=admin_user))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"],
        user_message="hola",
        user=admin_user,
        page_context={"route": "/decisions", "context_marker": "PAGECTXVALUE"},
    ))

    assert "PAGECTXVALUE" in seen["system"]
    for message in db.messages:
        assert "USER_PAGE_CONTEXT" not in str(message.get("content") or "")
        assert "PAGECTXVALUE" not in str(message.get("content") or "")


def test_control_room_page_context_adds_live_snapshot(
    copilot_module, db, admin_user, monkeypatch,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    seen: dict = {}
    _patch_llm(copilot_module, _capture_chat(seen))

    from app.services import copilot_page_context as cpc

    async def _kpis(_user):
        return {"readiness": {"profiled_employees": 3}}

    async def _overview(_user):
        return {"readiness": {"calculable_employees": 2}}

    async def _ops(_user):
        return {"items": {"total": 4}}

    async def _metadata(_user):
        return {"status": "partial"}

    async def _agents(_user, *, limit=12):
        return {"summary": {"active_agents": 2}, "limit": limit}

    monkeypatch.setattr(
        cpc.control_room_service, "sap_successfactors_talent_kpis", _kpis,
    )
    monkeypatch.setattr(
        cpc.control_room_service, "sap_successfactors_talent_overview", _overview,
    )
    monkeypatch.setattr(cpc.control_room_service, "ops_summary", _ops)
    monkeypatch.setattr(
        cpc.control_room_service,
        "sap_successfactors_talent_metadata_readiness",
        _metadata,
    )
    monkeypatch.setattr(cpc.control_room_service, "agents_ops", _agents)
    monkeypatch.setattr(
        cpc.copilot_context_service,
        "project_control_room_diagnostic",
        lambda _name, raw: raw,
    )

    conv = _run(copilot_module.create_conversation(user=admin_user))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"],
        user_message="estado",
        user=admin_user,
        page_context={"route": "/control-room", "title": "Control Room"},
    ))

    system = seen["system"]
    assert "live_control_room_snapshot" in system
    assert "profiled_employees" in system


def test_non_control_room_context_has_no_live_snapshot(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    seen: dict = {}
    _patch_llm(copilot_module, _capture_chat(seen))

    conv = _run(copilot_module.create_conversation(user=admin_user))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"],
        user_message="hola",
        user=admin_user,
        page_context={"route": "/dashboard"},
    ))
    assert "live_control_room_snapshot" not in seen["system"]


def _router_app(mod, effective_user):
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.dependencies import require_authenticated
    from app.services.csrf import require_csrf

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = effective_user
            return await call_next(request)

    api = FastAPI()
    api.add_middleware(_InjectUser)
    api.include_router(mod.router)
    api.dependency_overrides[require_authenticated] = lambda: effective_user
    api.dependency_overrides[require_csrf] = lambda: None
    return api


_ROUTER_USER = {
    "id": 7,
    "email": "u@example.com",
    "role": "admin",
    "active_tenant_id": "11111111-1111-1111-1111-111111111111",
    "active_workspace_id": "22222222-2222-2222-2222-222222222222",
}


def test_messages_endpoint_forwards_page_context(copilot_router):
    api = _router_app(copilot_router, _ROUTER_USER)
    captured = {}

    async def fake_run_turn(**kw):
        captured.update(kw)
        return {"reply": "ok"}

    copilot_router.copilot_service.run_turn = fake_run_turn

    async def fake_audit(**_kw):
        return None

    copilot_router.audit_service.record_event = fake_audit
    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages",
        json={"message": "hola", "page_context": {"route": "/decisions", "n": 3}},
    )
    assert r.status_code == 200, r.text
    assert captured["page_context"] == {"route": "/decisions", "n": 3}


def test_stream_endpoint_forwards_page_context(copilot_router):
    api = _router_app(copilot_router, _ROUTER_USER)
    captured = {}

    async def fake_open_turn_stream(**kw):
        captured.update(kw)

        async def events():
            yield {"type": "ready", "conversation_id": kw["conversation_id"]}
            yield {"type": "done", "result": {}}

        return events()

    copilot_router.copilot_service.open_turn_stream = fake_open_turn_stream

    async def fake_audit(**_kw):
        return None

    copilot_router.audit_service.record_event = fake_audit
    r = TestClient(api).post(
        "/api/copilot/chat/00000000-0000-0000-0000-000000000000/stream",
        json={"message": "hola", "page_context": {"route": "/dashboard"}},
    )
    assert r.status_code == 200, r.text
    assert captured["page_context"] == {"route": "/dashboard"}


def test_turn_request_rejects_unknown_keys(copilot_router):
    api = _router_app(copilot_router, _ROUTER_USER)
    invoked = []

    async def fake_run_turn(**kw):
        invoked.append(kw)
        return {"reply": "ok"}

    copilot_router.copilot_service.run_turn = fake_run_turn
    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages",
        json={"message": "hola", "evil_extra": True},
    )
    assert r.status_code == 422
    assert invoked == []


@pytest.mark.parametrize(
    "page_context",
    [
        {f"k{i}": "v" for i in range(25)},
        {"nested": {"a": 1}},
        {"listy": [1, 2]},
        {"long": "x" * 801},
        {"k" * 65: "v"},
    ],
)
def test_turn_request_rejects_invalid_page_context(copilot_router, page_context):
    api = _router_app(copilot_router, _ROUTER_USER)
    invoked = []

    async def fake_run_turn(**kw):
        invoked.append(kw)
        return {"reply": "ok"}

    copilot_router.copilot_service.run_turn = fake_run_turn
    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages",
        json={"message": "hola", "page_context": page_context},
    )
    assert r.status_code == 422
    assert invoked == []


def test_plain_message_body_still_works(copilot_router):
    api = _router_app(copilot_router, _ROUTER_USER)
    captured = {}

    async def fake_run_turn(**kw):
        captured.update(kw)
        return {"reply": "ok"}

    copilot_router.copilot_service.run_turn = fake_run_turn

    async def fake_audit(**_kw):
        return None

    copilot_router.audit_service.record_event = fake_audit
    r = TestClient(api).post(
        "/api/copilot/conversations/c1/messages",
        json={"message": "hola"},
    )
    assert r.status_code == 200, r.text
    assert captured["user_message"] == "hola"
    assert captured["page_context"] is None
