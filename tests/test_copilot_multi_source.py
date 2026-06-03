"""Sprint v1.43 — copilot multi-source orchestration.

Verifies:
  * A two-cartridge query produces one citation per cartridge.
  * The server-side guardrail trims to MAX_DISTINCT_SOURCES_PER_TURN
    when the LLM ignores the system-prompt cap.
  * Citations stay grouped by source in appearance order.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
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


class _FakeDB:
    def __init__(self):
        self.conversations = {}
        self.messages = []

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, user_id, workspace_id, title, created_at, updated_at FROM conversations"):
            return self.conversations.get(args[0])
        if q.startswith("INSERT INTO conversations"):
            cid = str(uuid.uuid4())
            row = {"id": cid, "user_id": args[0], "workspace_id": args[1],
                   "title": args[2], "created_at": "now", "updated_at": "now"}
            self.conversations[cid] = row
            return row
        if q.startswith("INSERT INTO conversation_messages"):
            mid = str(uuid.uuid4())
            self.messages.append({
                "id": mid, "conversation_id": args[0], "role": args[1],
                "content": args[2], "tool_calls": args[3],
                "tool_results": args[4], "citations": args[5],
                "model": args[6], "created_at": len(self.messages),
            })
            return {"id": mid}
        raise AssertionError(f"unmocked fetchrow: {q[:120]}")

    async def fetch(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT role, content, tool_calls, tool_results FROM conversation_messages"):
            return [m for m in self.messages if m["conversation_id"] == args[0]]
        raise AssertionError(f"unmocked fetch: {q[:120]}")

    async def execute(self, *a, **kw):
        return None


class _Acquire:
    def __init__(self, db): self.db = db
    async def __aenter__(self): return self.db
    async def __aexit__(self, *_): return False


class _Pool:
    def __init__(self, db): self.db = db
    def acquire(self): return _Acquire(self.db)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _patch(mod, db, tools, fake_chat, fake_invoke):
    mod.auth.pool = AsyncMock(return_value=_Pool(db))
    servers = {}
    for t in tools:
        srv, bare = t["name"].split("___", 1)
        servers.setdefault(srv, []).append({
            "name": bare, "description": "",
            "input_schema": {"type": "object", "properties": {}},
            "risk_level": t.get("risk_level", "read"),
            "requires_approval": t.get("requires_approval", False),
        })
    mod.tool_manifest.build_manifest = AsyncMock(return_value={
        "version": "1.0", "servers": servers,
        "tool_count_total": sum(len(v) for v in servers.values()),
    })

    async def _ev(**kw): pass
    mod.audit_service.record_event = _ev
    mod.mcp_registry.invoke = fake_invoke
    mod.llm_client.chat = fake_chat


def test_two_cartridge_query_returns_two_citations(copilot_module):
    db = _FakeDB()

    # The LLM calls one tool from Replicon and one from SAP HCM.
    async def fake_invoke(server_id, tool, args, **_kwargs):
        if server_id == "replicon":
            return {"_meta": {"entity": "TimeEntry", "row_count": 40,
                              "timestamp": "2026-05-16T10:00:00Z"}}
        if server_id == "sap_hcm":
            return {"_meta": {"entity": "Employee", "row_count": 1247,
                              "timestamp": "2026-05-16T09:30:00Z"}}
        return {"error": "unknown"}

    async def fake_chat(*, system, messages, tools, invoke_tool,
                        tool_server_map, on_event=None, **_kw):
        await invoke_tool("replicon", "list_entries",  {})
        await invoke_tool("sap_hcm",  "list_employees", {})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1",
                 "name": "replicon__list_entries", "input": {}},
                {"type": "tool_use", "id": "tu_2",
                 "name": "sap_hcm__list_employees", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"},
                {"type": "tool_result", "tool_use_id": "tu_2", "content": "{}"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Comparativa: 40h vs 1247 emp."},
            ]},
        ]
        return ("Comparativa: 40h vs 1247 emp.", [], final)

    _patch(copilot_module, db, [
        {"name": "replicon___list_entries", "risk_level": "read"},
        {"name": "sap_hcm___list_employees", "risk_level": "read"},
    ], fake_chat, fake_invoke)

    admin = {"id": 1, "email": "a@example.com", "role": "admin"}
    conv = _run(copilot_module.create_conversation(user_id=admin["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="compara horas vs emp",
        user=admin,
    ))

    sources = {c["source"] for c in out["citations"]}
    assert sources == {"replicon", "sap_hcm"}
    # And both citations are present.
    by_src = {c["source"]: c for c in out["citations"]}
    assert by_src["replicon"]["entity"] == "TimeEntry"
    assert by_src["sap_hcm"]["entity"]  == "Employee"


def test_source_cap_enforced_at_3(copilot_module, caplog):
    """If the LLM reaches for >3 cartridges, server trims citations to
    only the first 3 sources (in invocation order) and logs a warning."""
    db = _FakeDB()

    async def fake_invoke(server_id, tool, args, **_kwargs):
        return {"_meta": {"entity": "Foo", "row_count": 1,
                          "timestamp": "2026-05-16T00:00:00Z"}}

    async def fake_chat(*, messages, invoke_tool, **_kw):
        # Reach for 4 distinct sources — one over the cap.
        await invoke_tool("replicon",            "do_x", {})
        await invoke_tool("sap_hcm",             "do_x", {})
        await invoke_tool("sap_s4hana",          "do_x", {})
        await invoke_tool("sap_successfactors",  "do_x", {})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"tu_{i}",
                 "name": f"{srv}__do_x", "input": {}}
                for i, srv in enumerate(
                    ["replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"]
                )
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"tu_{i}", "content": "{}"}
                for i in range(4)
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "respuesta"},
            ]},
        ]
        return ("respuesta", [], final)

    _patch(copilot_module, db, [
        {"name": "replicon___do_x",            "risk_level": "read"},
        {"name": "sap_hcm___do_x",             "risk_level": "read"},
        {"name": "sap_s4hana___do_x",          "risk_level": "read"},
        {"name": "sap_successfactors___do_x",  "risk_level": "read"},
    ], fake_chat, fake_invoke)

    admin = {"id": 2, "email": "b@example.com", "role": "admin"}
    conv = _run(copilot_module.create_conversation(user_id=admin["id"]))
    import logging
    with caplog.at_level(logging.WARNING):
        out = _run(copilot_module.run_turn(
            conversation_id=conv["id"], user_message="cuéntame todo",
            user=admin,
        ))

    surfaced_sources = {c["source"] for c in out["citations"]}
    assert len(surfaced_sources) == copilot_module.MAX_DISTINCT_SOURCES_PER_TURN
    # Insertion order: the FIRST 3 reached survive.
    assert surfaced_sources == {"replicon", "sap_hcm", "sap_s4hana"}
    # The 4th (sap_successfactors) was trimmed.
    assert "sap_successfactors" not in surfaced_sources
    # And the warning was logged.
    assert any("multi_source_limit_exceeded" in r.message for r in caplog.records)


def test_citations_grouped_by_source_in_order(copilot_module):
    """Citations come back in the order they were invoked. The UI uses
    that ordering to render cards left-to-right."""
    db = _FakeDB()

    async def fake_invoke(server_id, tool, args, **_kwargs):
        return {"_meta": {"entity": tool, "row_count": 1,
                          "timestamp": "2026-05-16T00:00:00Z"}}

    async def fake_chat(*, messages, invoke_tool, **_kw):
        await invoke_tool("sap_hcm",   "list_employees", {})
        await invoke_tool("replicon",  "list_entries",   {})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1",
                 "name": "sap_hcm__list_employees", "input": {}},
                {"type": "tool_use", "id": "tu_2",
                 "name": "replicon__list_entries", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}"},
                {"type": "tool_result", "tool_use_id": "tu_2", "content": "{}"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "ok"},
            ]},
        ]
        return ("ok", [], final)

    _patch(copilot_module, db, [
        {"name": "sap_hcm___list_employees", "risk_level": "read"},
        {"name": "replicon___list_entries",  "risk_level": "read"},
    ], fake_chat, fake_invoke)

    admin = {"id": 3, "email": "c@example.com", "role": "admin"}
    conv = _run(copilot_module.create_conversation(user_id=admin["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="dame ambos",
        user=admin,
    ))
    ordered = [c["source"] for c in out["citations"]]
    # sap_hcm came first → its citation appears first.
    assert ordered[0] == "sap_hcm"
    assert ordered[1] == "replicon"


def test_system_prompt_mentions_multi_source_limit(copilot_module):
    p = copilot_module.SYSTEM_PROMPT
    assert "MULTI-FUENTE" in p
    assert "máximo 3 cartuchos" in p
