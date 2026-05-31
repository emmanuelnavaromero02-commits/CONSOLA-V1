"""Sprint v1.45 cúspide — verify the copilot_service._run_loop calls
the lessons injector and stitches the result into the system prompt
sent to the LLM.

We import copilot_service and patch the two collaborators
(``memory_service.build_system_prompt_with_memory`` and
``lessons_service.build_system_prompt_with_lessons``) so the run loop
can exercise the injection path without a live DB or LLM. The assertion
is that the system prompt the LLM ultimately receives contains the
lessons block when lessons_service returns one.
"""
from __future__ import annotations

import asyncio
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
    """When lessons_service.build_system_prompt_with_lessons returns a
    base+lessons string, llm_client.chat must receive THAT prompt (not
    the bare SYSTEM_PROMPT). This is the contract the v1.45 cúspide
    upgrade put in place — regress on it and lessons silently stop
    flowing into the LLM."""

    captured: dict = {}

    async def fake_chat(*, system, messages, tools, invoke_tool,
                        tool_server_map, on_event=None,
                        model=None, max_tokens=None, temperature=None):
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

    monkeypatch.setattr(copilot_mod.auth, "pool", fake_pool)
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

    out = asyncio.get_event_loop().run_until_complete(
        copilot_mod._run_loop(
            conversation_id="11111111-1111-1111-1111-111111111111",
            user={"id": 1, "email": "u@x", "active_workspace_id": None,
                  "role": "admin"},
            ip=None, user_agent=None,
            approved_keys=set(),
        )
    )
    assert captured.get("system"), "llm_client.chat was never called"
    assert "<LEARNED_LESSONS>" in captured["system"], (
        "lessons block missing from system prompt"
    )
    assert "regla A" in captured["system"]
    # intent_hint should be the user's last message
    assert captured.get("intent_hint") == "revisa cartera vencida"
    # Memory block also present (chain preserved)
    assert "Contexto del usuario" in captured["system"]
