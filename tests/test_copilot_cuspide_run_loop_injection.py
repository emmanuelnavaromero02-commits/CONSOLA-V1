from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def copilot_mod():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import copilot_service as mod
    return mod


def test_run_loop_injects_lessons_block_into_system_prompt(
    copilot_mod, monkeypatch,
):

    captured: dict = {}

    async def fake_chat(*, system, messages, tools, invoke_tool,
                        tool_server_map, on_event=None,
                        model=None, max_tokens=None, temperature=None, **_kw):
        captured["system"] = system
        captured["messages"] = messages
        return ("respuesta mock", [], messages)

    async def fake_memory(uid, base):
        return base + "\n\n## Contexto del usuario\n- foo\n"

    async def fake_lessons(*, user_id, workspace_id, base_prompt,
                           intent_hint=None):
        captured["intent_hint"] = intent_hint
        return base_prompt + "\n\n<LEARNED_LESSONS>\n  <lesson>regla A</lesson>\n</LEARNED_LESSONS>\n"

    async def fake_load_conv(conn, conversation_id):
        return {"id": conversation_id, "user_id": 1, "title": "x"}

    async def fake_load_history(conn, conversation_id):
        return [{"role": "user", "content": "revisa cartera vencida"}]

    async def fake_tools():
        return [], {}, {}

    async def fake_persist(*args, **kwargs):
        return "msg-id-stub"

    class _FakeConn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None

    class _FakePool:
        def acquire(self): return _FakeConn()

    async def fake_pool():
        return _FakePool()

    @asynccontextmanager
    async def fake_scoped_db_for_user(pool, user):
        yield _FakeConn(), user.get("active_tenant_id"), user.get("active_workspace_id")

    monkeypatch.setattr(copilot_mod.auth, "pool", fake_pool)
    monkeypatch.setattr(copilot_mod, "scoped_db_for_user", fake_scoped_db_for_user)
    monkeypatch.setattr(copilot_mod, "_load_conversation", fake_load_conv)
    monkeypatch.setattr(copilot_mod, "_load_history", fake_load_history)
    monkeypatch.setattr(copilot_mod, "_build_tools_for_llm", fake_tools)
    monkeypatch.setattr(copilot_mod, "_persist_message", fake_persist)
    monkeypatch.setattr(copilot_mod, "_maybe_extract_facts", AsyncMock())
    monkeypatch.setattr(copilot_mod.llm_client, "chat", fake_chat)
    monkeypatch.setattr(
        copilot_mod.memory_service, "build_system_prompt_with_memory",
        fake_memory,
    )
    monkeypatch.setattr(
        copilot_mod.lessons_service, "build_system_prompt_with_lessons",
        fake_lessons,
    )

    out = asyncio.run(
        copilot_mod._run_loop(
            conversation_id="11111111-1111-1111-1111-111111111111",
            user={
                "id": 1,
                "email": "u@x",
                "active_tenant_id": "tenant-a",
                "active_workspace_id": "workspace-a",
                "role": "admin",
            },
            ip=None, user_agent=None,
            approved_keys=set(),
        )
    )
    assert captured.get("system"), "llm_client.chat was never called"
    assert "<LEARNED_LESSONS>" in captured["system"], (
        "lessons block missing from system prompt"
    )
    assert "regla A" in captured["system"]
    assert captured.get("intent_hint") == "revisa cartera vencida"
    assert "Contexto del usuario" in captured["system"]
