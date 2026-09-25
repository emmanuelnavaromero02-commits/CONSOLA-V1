from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def copilot_module():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import copilot_service as mod
    return mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_first_attempt_success_no_retry(copilot_module, monkeypatch):
    calls = []
    async def fake(server, tool, args, **_kwargs):
        calls.append((server, tool))
        return {"ok": True}
    monkeypatch.setattr(copilot_module.mcp_registry, "invoke", fake)

    out = _run(copilot_module._invoke_tool_with_retry("srv", "t", {}))
    assert out == {"ok": True}
    assert len(calls) == 1


def test_retry_on_transient_failure_then_success(copilot_module, monkeypatch):
    attempts = []
    async def fake(server, tool, args, **_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("transient")
        return {"ok": True, "rows": [1, 2]}
    monkeypatch.setattr(copilot_module.mcp_registry, "invoke", fake)

    slept = []
    async def fake_sleep(d):
        slept.append(d)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    out = _run(copilot_module._invoke_tool_with_retry("srv", "t", {}))
    assert out == {"ok": True, "rows": [1, 2]}
    assert len(attempts) == 3
    assert slept == [1.0, 2.0]


def test_retry_exhaustion_returns_user_friendly_envelope(copilot_module, monkeypatch):
    async def fake(*a, **kw):
        raise TimeoutError("ECONNREFUSED")
    monkeypatch.setattr(copilot_module.mcp_registry, "invoke", fake)
    async def fake_sleep(_d): pass
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    out = _run(copilot_module._invoke_tool_with_retry("sap_hcm", "get_emp", {}))
    assert out["_error"] is True
    assert out["error_type"] == "TimeoutError"
    assert out["server"] == "sap_hcm"
    assert out["tool"]   == "get_emp"
    msg = out["_meta"]["user_facing"]
    assert "sap_hcm" in msg
    assert "3 intentos" in msg


def test_retry_error_envelope_strips_internals(copilot_module, monkeypatch):
    async def fake(*a, **kw):
        raise RuntimeError(
            "401 Unauthorized: Authorization: Bearer sk-LEAK-12345 to upstream"
        )
    monkeypatch.setattr(copilot_module.mcp_registry, "invoke", fake)
    async def fake_sleep(_d): pass
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    out = _run(copilot_module._invoke_tool_with_retry("srv", "t", {}))
    assert out["_error"]
    assert "sk-LEAK-12345" not in out["error_message"]
    assert "Bearer ***" in out["error_message"]


def test_retry_max_attempts_constant(copilot_module):
    assert copilot_module._TOOL_RETRY_MAX_ATTEMPTS == 3
    assert copilot_module._TOOL_RETRY_BASE_DELAY_S == 1.0


def test_error_envelope_propagates_to_llm_as_tool_result(copilot_module, monkeypatch):
    seen_results = []

    async def fake_invoke(server, tool, args, **_kwargs):
        raise ConnectionError("upstream down")
    monkeypatch.setattr(copilot_module.mcp_registry, "invoke", fake_invoke)
    async def fake_sleep(_d): pass
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    import uuid
    class _DB:
        def __init__(self):
            self.conversations = {}
            self.messages = []

        def transaction(self):
            return _Transaction()

        async def fetchrow(self, q, *a):
            qq = " ".join(q.split())
            if qq.startswith("SELECT id, user_id, workspace_id, title"):
                return self.conversations.get(a[0])
            if qq.startswith("INSERT INTO conversations"):
                cid = str(uuid.uuid4())
                row = {"id": cid, "user_id": a[0], "workspace_id": a[1],
                       "title": a[2], "created_at": "now", "updated_at": "now"}
                self.conversations[cid] = row
                return row
            if qq.startswith("INSERT INTO conversation_messages"):
                mid = str(uuid.uuid4())
                self.messages.append({
                    "id": mid, "conversation_id": a[0], "role": a[1],
                    "content": a[2], "tool_calls": a[3], "tool_results": a[4],
                    "citations": a[5], "model": a[6],
                })
                return {"id": mid}
            raise AssertionError(f"unmocked fetchrow: {qq[:120]}")

        async def fetch(self, q, *a):
            qq = " ".join(q.split())
            if qq.startswith("SELECT role, content, tool_calls, tool_results FROM"):
                return [m for m in self.messages if m["conversation_id"] == a[0]]
            raise AssertionError(f"unmocked fetch: {qq[:120]}")

        async def execute(self, *a, **kw):
            return None

    class _Transaction:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False

    class _Acq:
        def __init__(self, db): self.db = db
        async def __aenter__(self): return self.db
        async def __aexit__(self, *_): return False

    class _Pool:
        def __init__(self, db): self.db = db
        def acquire(self): return _Acq(self.db)

    db = _DB()
    copilot_module.auth.pool = AsyncMock(return_value=_Pool(db))
    copilot_module.tool_manifest.build_manifest = AsyncMock(return_value={
        "version": "1.0",
        "servers": {
            "sap_hcm": [{"name": "get_employees", "description": "",
                         "input_schema": {"type": "object", "properties": {}},
                         "risk_level": "read", "requires_approval": False}],
        },
        "tool_count_total": 1,
    })
    async def _ev(**kw): pass
    copilot_module.audit_service.record_event = _ev

    async def fake_chat(*, messages, invoke_tool, **_kw):
        result = await invoke_tool("sap_hcm", "get_employees", {})
        seen_results.append(result)
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1",
                 "name": "sap_hcm__get_employees", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1",
                 "content": '{"_error":true}'},
            ]},
            {"role": "assistant", "content": [
                {"type": "text",
                 "text": "No pude conectar con SAP HCM — reintenta en unos minutos."},
            ]},
        ]
        return ("No pude conectar con SAP HCM — reintenta en unos minutos.",
                [], final)

    copilot_module.llm_client.chat = fake_chat

    admin = {
        "id": 1,
        "email": "a@example.com",
        "role": "admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
    }
    conv = _run(copilot_module.create_conversation(user=admin))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="cuántos empleados?",
        user=admin,
    ))

    assert seen_results, "invoke_tool must have been called"
    envelope = seen_results[0]
    assert envelope["_error"] is True
    assert envelope["server"] == "sap_hcm"
    assert "No pude conectar" in out["reply"]
    assert out["citations"] == []
