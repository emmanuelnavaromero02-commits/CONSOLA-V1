"""Sprint v1.45 cúspide audit round 3 — observability, resilience and
forensic-chain regression guards.

These tests pin the surfaces added by the round-3 review:

* ``briefing_v2_for_user`` returns ``[]`` when the upstream proactive
  analyzers raise (no 500 cascade onto the dashboard).
* ``_llm_text_call`` failures map to 504/502 in the router, not 500.
* ``record_lesson_from_approval`` and ``record_lesson_from_decline``
  emit a ``copilot.lesson.recorded_from_{approval,decline}`` audit
  event so the forensic chain between an approval and the durable
  lesson it planted is reconstructible.
* ``POST /goals`` and ``POST /watchdogs/.../invoke`` emit their own
  audit events at the router layer.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")

_GOAL_ID = "66666666-6666-6666-6666-666666666666"


@pytest.fixture
def cuspide_modules():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.routers import copilot_advanced
    from app.services import (
        briefing_v2, goal_solver, lessons_service, watchdog_registry,
    )
    from app.services._copilot_helpers import reset_table_cache
    reset_table_cache()
    return {
        "router": copilot_advanced,
        "briefing_v2": briefing_v2,
        "goal_solver": goal_solver,
        "lessons_service": lessons_service,
        "watchdog_registry": watchdog_registry,
    }


def _admin_user(uid=7):
    return {
        "id": uid,
        "email": f"u{uid}@example.com",
        "role": "admin",
        "active_workspace_id": 1,
        "permissions": ["copilot.use", "copilot.write"],
        "permission_set": {"copilot.use", "copilot.write"},
    }


def _mount(router_mod, *, user):
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.dependencies import require_authenticated
    from app.services.csrf import require_csrf

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = user
            return await call_next(request)

    api = FastAPI()
    api.add_middleware(_InjectUser)
    api.include_router(router_mod.router)
    api.dependency_overrides[require_authenticated] = lambda: user
    api.dependency_overrides[require_csrf] = lambda: None
    return api


# ── briefing_v2 defensive against upstream failure ────────────────────


def test_briefing_v2_returns_empty_when_upstream_raises(cuspide_modules, monkeypatch):
    bm = cuspide_modules["briefing_v2"]

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated proactive_service DB error")

    monkeypatch.setattr(bm.proactive_service, "briefing_for_user", boom)
    out = asyncio.run(bm.briefing_v2_for_user(user_id=7, limit=6))
    assert out == []


def test_briefing_v2_endpoint_does_not_leak_upstream_500(cuspide_modules, monkeypatch):
    """End-to-end: even if the proactive analyzers throw, the dashboard
    sees a clean 200 with an empty array, not a 500."""
    api = _mount(cuspide_modules["router"], user=_admin_user())

    async def boom(*args, **kwargs):
        raise RuntimeError("schema drift")

    monkeypatch.setattr(
        cuspide_modules["briefing_v2"].proactive_service,
        "briefing_for_user", boom,
    )
    r = TestClient(api).get("/api/copilot/briefing/v2")
    assert r.status_code == 200
    assert r.json() == []


# ── LLM failure → 504 / 502 sanitised, no leak ────────────────────────


def test_diagnose_goal_504_on_llm_timeout(cuspide_modules, monkeypatch):
    api = _mount(cuspide_modules["router"], user=_admin_user())

    async def timeout_llm(system, messages, **_kw):
        raise RuntimeError("llm_call_timeout")

    async def diag_calls(goal_id, user_id, llm_call, **_scope):
        # Invoke the llm so the conversion to RuntimeError happens
        # via the real router-level adapter.
        return await llm_call("x", [])

    monkeypatch.setattr(
        cuspide_modules["router"], "_llm_text_call", timeout_llm,
    )
    monkeypatch.setattr(
        cuspide_modules["goal_solver"], "diagnose_goal",
        diag_calls,
    )
    r = TestClient(api).post(f"/api/copilot/goals/{_GOAL_ID}/diagnose")
    assert r.status_code == 504
    assert "timed out" in r.text


def test_diagnose_goal_502_on_llm_generic_failure(cuspide_modules, monkeypatch):
    api = _mount(cuspide_modules["router"], user=_admin_user())

    async def boom_llm(system, messages):
        raise RuntimeError("llm_call_failed")

    async def diag_calls(goal_id, user_id, llm_call, **_scope):
        return await llm_call("x", [])

    monkeypatch.setattr(
        cuspide_modules["router"], "_llm_text_call", boom_llm,
    )
    monkeypatch.setattr(
        cuspide_modules["goal_solver"], "diagnose_goal",
        diag_calls,
    )
    r = TestClient(api).post(f"/api/copilot/goals/{_GOAL_ID}/diagnose")
    assert r.status_code == 502
    # Body does not echo "RuntimeError" / "llm_call_failed" — those
    # would leak the upstream provider error shape.
    assert "RuntimeError" not in r.text
    assert "llm_call_failed" not in r.text


def test_ask_with_context_504_on_timeout(cuspide_modules, monkeypatch):
    api = _mount(cuspide_modules["router"], user=_admin_user())

    async def timeout_llm(system, messages, **_kw):
        raise RuntimeError("llm_call_timeout")

    monkeypatch.setattr(
        cuspide_modules["router"], "_llm_text_call", timeout_llm,
    )
    r = TestClient(api).post(
        "/api/copilot/ask-with-context",
        json={"question": "¿estado de cartera?"},
    )
    assert r.status_code == 504


# ── _llm_text_call sanitises arbitrary upstream exceptions ────────────


def test_llm_text_call_converts_unknown_exception_to_runtime_error(cuspide_modules, monkeypatch):
    """``_llm_text_call`` must NEVER re-raise the raw upstream
    exception type — that would let a ``ConnectionError`` or
    provider-specific ``AnthropicError`` reach the FastAPI 500 path
    with a traceback that names the provider in the response."""
    router_mod = cuspide_modules["router"]

    async def evil_chat(**_kwargs):
        # Pretend the provider raised a custom error class whose name
        # would leak implementation details.
        raise ConnectionError("anthropic.AnthropicProviderError")

    monkeypatch.setattr(router_mod.llm_client, "chat", evil_chat)
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(router_mod._llm_text_call("system", [{"role": "user", "content": "x"}]))
    # The sanitised marker, not the upstream type.
    assert str(exc_info.value) == "llm_call_failed"


def test_llm_text_call_preserves_value_error(cuspide_modules, monkeypatch):
    """ValueError is the contract used by ``goal_solver.parse_diagnosis``
    to signal bad JSON. The adapter must NOT wrap it — the router's
    ``except ValueError`` branch must still fire."""
    router_mod = cuspide_modules["router"]

    async def evil_chat(**_kwargs):
        raise ValueError("bad json from provider")

    monkeypatch.setattr(router_mod.llm_client, "chat", evil_chat)
    with pytest.raises(ValueError):
        asyncio.run(router_mod._llm_text_call("s", [{"role": "user", "content": "x"}]))


# ── Audit forensic chain on approval / decline lessons ───────────────


def test_record_lesson_from_approval_emits_audit(cuspide_modules, monkeypatch):
    lessons_mod = cuspide_modules["lessons_service"]

    # FakePool that says the table is present and returns a deterministic id.
    class FakePool:
        def __init__(self):
            self._fetchval_q = ["copilot_lessons", None]
            self._fetchrow_q = [{"id": "lesson-id-approval"}]
        async def fetchval(self, *a, **kw):
            return self._fetchval_q.pop(0) if self._fetchval_q else None
        async def fetchrow(self, *a, **kw):
            return self._fetchrow_q.pop(0) if self._fetchrow_q else None
        async def fetch(self, *a, **kw): return []
        async def execute(self, *a, **kw): return "UPDATE 1"

    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=FakePool()))

    captured: dict = {}

    async def capture_event(**kwargs):
        captured.update(kwargs)

    # Patch the lazy import inside _emit_lesson_audit.
    import app.services.audit_service as _audit_mod
    monkeypatch.setattr(_audit_mod, "record_event", capture_event)

    out = asyncio.run(lessons_mod.record_lesson_from_approval(
        user_id=42,
        workspace_id=None,
        tool_name="replicon.create_dag",
        tool_args={"name": "monthly_close"},
        conversation_id="44444444-4444-4444-4444-444444444444",
    ))
    assert out == "lesson-id-approval"
    assert captured.get("action") == "copilot.lesson.recorded_from_approval"
    assert captured.get("resource_id") == "lesson-id-approval"
    assert captured.get("user_id") == 42


def test_record_lesson_from_decline_emits_audit(cuspide_modules, monkeypatch):
    lessons_mod = cuspide_modules["lessons_service"]

    class FakePool:
        def __init__(self):
            self._fetchval_q = ["copilot_lessons", None]
            self._fetchrow_q = [{"id": "lesson-id-decline"}]
        async def fetchval(self, *a, **kw):
            return self._fetchval_q.pop(0) if self._fetchval_q else None
        async def fetchrow(self, *a, **kw):
            return self._fetchrow_q.pop(0) if self._fetchrow_q else None
        async def fetch(self, *a, **kw): return []
        async def execute(self, *a, **kw): return "UPDATE 1"

    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=FakePool()))

    captured: dict = {}

    async def capture_event(**kwargs):
        captured.update(kwargs)

    import app.services.audit_service as _audit_mod
    monkeypatch.setattr(_audit_mod, "record_event", capture_event)

    out = asyncio.run(lessons_mod.record_lesson_from_decline(
        user_id=42,
        workspace_id=None,
        tool_name="replicon.delete_dag",
        tool_args={"name": "monthly_close"},
        reason="not now",
    ))
    assert out == "lesson-id-decline"
    assert captured.get("action") == "copilot.lesson.recorded_from_decline"


def test_audit_failure_does_not_break_lesson_recording(cuspide_modules, monkeypatch):
    """If the audit emission throws (e.g. audit_events table missing),
    the lesson must still be persisted and returned. The whole point
    of the lazy-import + best-effort wrapper is that an observability
    outage cannot break the learning loop."""
    lessons_mod = cuspide_modules["lessons_service"]

    class FakePool:
        def __init__(self):
            self._fetchval_q = ["copilot_lessons", None]
            self._fetchrow_q = [{"id": "lesson-still-recorded"}]
        async def fetchval(self, *a, **kw):
            return self._fetchval_q.pop(0) if self._fetchval_q else None
        async def fetchrow(self, *a, **kw):
            return self._fetchrow_q.pop(0) if self._fetchrow_q else None
        async def fetch(self, *a, **kw): return []
        async def execute(self, *a, **kw): return "UPDATE 1"

    monkeypatch.setattr(lessons_mod.auth, "pool", AsyncMock(return_value=FakePool()))

    async def boom_event(**kwargs):
        raise RuntimeError("audit_events table is gone")

    import app.services.audit_service as _audit_mod
    monkeypatch.setattr(_audit_mod, "record_event", boom_event)

    out = asyncio.run(lessons_mod.record_lesson_from_approval(
        user_id=42, workspace_id=None,
        tool_name="x.y", tool_args={},
    ))
    assert out == "lesson-still-recorded"


# ── Router-level audit emission for goals and watchdogs ──────────────


def test_create_goal_emits_audit(cuspide_modules, monkeypatch):
    api = _mount(cuspide_modules["router"], user=_admin_user())

    monkeypatch.setattr(
        cuspide_modules["goal_solver"], "create_goal",
        AsyncMock(return_value={"id": _GOAL_ID, "status": "planning"}),
    )
    captured: dict = {}

    async def capture_event(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        cuspide_modules["router"].audit_service, "record_event", capture_event,
    )
    r = TestClient(api).post(
        "/api/copilot/goals",
        json={"goal_text": "Mejora el margen este trimestre"},
    )
    assert r.status_code == 200
    assert captured.get("action") == "copilot.goal.created"
    assert captured.get("resource_id") == _GOAL_ID


def test_invoke_watchdog_emits_audit(cuspide_modules, monkeypatch):
    api = _mount(cuspide_modules["router"], user=_admin_user())

    monkeypatch.setattr(
        cuspide_modules["watchdog_registry"], "invoke_watchdog",
        AsyncMock(return_value={
            "watchdog": {"slug": "margin"},
            "mode": "tool_catalog",
            "tools": ["replicon.query_kb"],
        }),
    )
    captured: dict = {}

    async def capture_event(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        cuspide_modules["router"].audit_service, "record_event", capture_event,
    )
    r = TestClient(api).post(
        "/api/copilot/watchdogs/replicon/margin_watchdog/invoke",
        json={"input_text": "revisa margen"},
    )
    assert r.status_code == 200
    assert captured.get("action") == "copilot.watchdog.invoked"
    assert captured.get("resource_id") == "replicon/margin_watchdog"


# ── SYSTEM_PROMPT documents the LEARNED_LESSONS section (round 3) ────


def test_system_prompt_documents_learned_lessons_block():
    """Audit-round-3: the base SYSTEM_PROMPT must mention the
    ``<LEARNED_LESSONS>`` block that the lessons service injects,
    so the LLM knows to treat it as advisory data rather than as new
    system rules. Without this hint, the model's behaviour around
    contradictory or injected lessons is undefined."""
    from app.services.copilot_service import SYSTEM_PROMPT
    assert "LEARNED_LESSONS" in SYSTEM_PROMPT
    # And the contradiction-resolution rule must be explicit.
    assert "IGNORA LA LECCIÓN" in SYSTEM_PROMPT or "ignora la lección" in SYSTEM_PROMPT.lower()
