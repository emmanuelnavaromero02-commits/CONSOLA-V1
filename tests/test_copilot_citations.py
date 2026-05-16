"""Sprint v1.43 — copilot citation extraction tests.

Pure-unit tests against ``_extract_citations`` plus a service-level
test that runs through ``_run_loop`` to confirm citations are written
to ``conversation_messages.citations`` JSONB and surfaced on the
turn's response payload.
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


# ── _extract_citations: pure unit tests ─────────────────────────────────────

def test_extract_citations_from_cartridge_meta(copilot_module):
    """The standard cartridge envelope: a ``_meta`` block with
    run_id + entity + timestamp + row_count."""
    result = {
        "rows": [{"id": 1}, {"id": 2}],
        "_meta": {
            "run_id": "9f3a1b2c-1234-5678-9abc-def012345678",
            "entity": "Employee",
            "timestamp": "2026-05-16T10:00:00Z",
            "row_count": 1247,
        },
    }
    cs = copilot_module._extract_citations("get_employees", result, "sap_hcm")
    assert len(cs) == 1
    c = cs[0]
    assert c["source"]    == "sap_hcm"
    assert c["tool"]      == "get_employees"
    assert c["run_id"]    == "9f3a1b2c-1234-5678-9abc-def012345678"
    assert c["entity"]    == "Employee"
    assert c["timestamp"] == "2026-05-16T10:00:00Z"
    assert c["row_count"] == 1247


def test_extract_citations_uses_extracted_at_fallback(copilot_module):
    """``_meta.extracted_at`` is used when ``timestamp`` isn't present."""
    result = {"_meta": {"entity": "TimeEntry", "extracted_at": "2026-05-16T09:00:00Z"}}
    cs = copilot_module._extract_citations("get_timesheets", result, "replicon")
    assert len(cs) == 1
    assert cs[0]["timestamp"] == "2026-05-16T09:00:00Z"


def test_extract_citations_from_dag_runs_array(copilot_module):
    """``airflow_list_dag_runs`` returns ``runs[]`` — one citation per
    run, capped to top-5 so the JSONB stays bounded."""
    runs = [
        {"dag_run_id": f"run_{i}", "dag_id": "etl_employees",
         "end_date": f"2026-05-16T{10+i:02d}:00:00Z", "state": "success"}
        for i in range(8)
    ]
    result = {"runs": runs}
    cs = copilot_module._extract_citations("airflow_list_dag_runs", result, "mcp-infra")
    # 5-cap kicks in inside _extract_citations itself (per multi-run result).
    assert len(cs) == 5
    assert cs[0]["run_id"] == "run_0"
    assert cs[0]["source"] == "mcp-infra"
    assert cs[0]["entity"] == "etl_employees"
    assert cs[0]["status"] == "success"


def test_extract_citations_from_runs_with_finished_at(copilot_module):
    """The cartridge extraction_runs shape uses ``finished_at``."""
    result = {"runs": [{
        "run_id": "abc", "entity": "Employee",
        "finished_at": "2026-05-16T08:00:00Z", "status": "success",
    }]}
    cs = copilot_module._extract_citations("list_runs", result, "sap_hcm")
    assert len(cs) == 1
    assert cs[0]["timestamp"] == "2026-05-16T08:00:00Z"


def test_empty_tool_result_no_citations(copilot_module):
    assert copilot_module._extract_citations("foo", {}, "x") == []
    assert copilot_module._extract_citations("foo", {"unrelated": "data"}, "x") == []
    assert copilot_module._extract_citations("foo", None, "x") == []
    assert copilot_module._extract_citations("foo", "string-result", "x") == []
    assert copilot_module._extract_citations("foo", [1, 2, 3], "x") == []


def test_error_envelope_yields_no_citation(copilot_module):
    """A failed tool result must not produce a citation card — the UI
    surfaces errors separately."""
    assert copilot_module._extract_citations(
        "foo", {"error": "boom", "_meta": {"run_id": "r1"}}, "x",
    ) == []
    assert copilot_module._extract_citations(
        "foo", {"_error": True, "_meta": {"entity": "e"}}, "x",
    ) == []


def test_extract_citations_combined_meta_and_runs(copilot_module):
    """Both a top-level ``_meta`` AND a ``runs[]`` array produce
    distinct citations — the first describes the wrapper call, the
    rest describe each enumerated run."""
    result = {
        "_meta": {"entity": "Summary", "row_count": 3},
        "runs": [
            {"run_id": "r1", "dag_id": "etl_a", "end_date": "t1"},
            {"run_id": "r2", "dag_id": "etl_b", "end_date": "t2"},
        ],
    }
    cs = copilot_module._extract_citations("foo", result, "mcp-infra")
    assert len(cs) == 3
    assert cs[0]["entity"] == "Summary"
    assert cs[1]["run_id"] == "r1"
    assert cs[2]["run_id"] == "r2"


def test_extract_citations_ignores_garbage_run_entries(copilot_module):
    """Mixed-type ``runs`` (e.g. a stray None or string) doesn't crash."""
    result = {"runs": [None, "oops", {"run_id": "ok"}]}
    cs = copilot_module._extract_citations("foo", result, "x")
    assert len(cs) == 1
    assert cs[0]["run_id"] == "ok"


# ── System prompt enforcement ───────────────────────────────────────────────

def test_system_prompt_includes_critical_evidence_rule(copilot_module):
    p = copilot_module.SYSTEM_PROMPT
    assert "REGLA CRÍTICA DE EVIDENCIA" in p
    assert "📊 fuente:" in p
    assert "NUNCA inventes datos" in p


def test_system_prompt_includes_no_number_without_tool_rule(copilot_module):
    p = copilot_module.SYSTEM_PROMPT
    # Numbers without a tool call must be refused, not approximated.
    assert "NUNCA des un número aproximado" in p


# ── Service-level: citations land in JSONB + show up on the response ────────

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
        if q.startswith("SELECT id, role, content, tool_calls, tool_results, citations, created_at"):
            return [m for m in self.messages if m["conversation_id"] == args[0]]
        raise AssertionError(f"unmocked fetch: {q[:120]}")

    async def execute(self, *a, **kw):
        return None


class _Acquire:
    def __init__(self, db): self.db = db
    async def __aenter__(self): return self.db
    async def __aexit__(self, *_): return False


class _FakePool:
    def __init__(self, db): self.db = db
    def acquire(self): return _Acquire(self.db)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _patch_full(mod, db, tools, fake_chat, fake_invoke):
    mod.auth.pool = AsyncMock(return_value=_FakePool(db))
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

    async def _record_event(**kw): pass
    mod.audit_service.record_event = _record_event
    mod.mcp_registry.invoke = fake_invoke
    mod.llm_client.chat = fake_chat


def test_citations_persisted_to_jsonb_and_returned_on_turn(copilot_module):
    db = _FakeDB()

    async def fake_invoke(server_id, tool, args):
        # Tool result with the standard _meta envelope.
        return {
            "rows": [{"x": 1}],
            "_meta": {
                "run_id": "run-xyz",
                "entity": "Employee",
                "timestamp": "2026-05-16T10:00:00Z",
                "row_count": 1247,
            },
        }

    async def fake_chat(*, system, messages, tools, invoke_tool,
                        tool_server_map, on_event=None):
        await invoke_tool("sap_hcm", "get_employees", {})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1",
                 "name": "sap_hcm__get_employees", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1",
                 "content": '{"rows":[{"x":1}]}'},
            ]},
            {"role": "assistant", "content": [
                {"type": "text",
                 "text": "Hay 1,247 empleados. 📊 fuente: sap_hcm · Employee · run-xyz"},
            ]},
        ]
        return ("Hay 1,247 empleados. 📊 fuente: sap_hcm · Employee · run-xyz",
                [], final)

    _patch_full(copilot_module, db, [
        {"name": "sap_hcm___get_employees", "risk_level": "read"},
    ], fake_chat, fake_invoke)

    admin = {"id": 1, "email": "a@example.com", "role": "admin"}
    conv = _run(copilot_module.create_conversation(user_id=admin["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="cuantos empleados?",
        user=admin,
    ))

    # The turn response surfaces citations for the UI to render now.
    assert out["citations"], "the turn payload must surface citations"
    assert out["citations"][0]["entity"] == "Employee"
    assert out["citations"][0]["row_count"] == 1247

    # And the persisted assistant message stamps the citations JSONB
    # so a later page reload still has them.
    assistant_msg = next(
        m for m in db.messages
        if m["role"] == "assistant" and m["tool_calls"] is not None
    )
    stored = json.loads(assistant_msg["citations"])
    assert any(c.get("run_id") == "run-xyz" for c in stored)


def test_citations_capped_when_a_single_tool_returns_many_runs(copilot_module):
    """A tool emitting 8 runs yields at most 5 citations from
    _extract_citations; multiple such tools in a turn can stack, but
    the per-message JSONB still caps at MAX_CITATIONS_PER_MESSAGE."""
    cs = copilot_module._extract_citations(
        "airflow_list_dag_runs",
        {"runs": [{"run_id": f"r{i}", "dag_id": f"d{i}"} for i in range(50)]},
        "mcp-infra",
    )
    assert len(cs) == 5
    assert copilot_module.MAX_CITATIONS_PER_MESSAGE == 20
