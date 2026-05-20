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

    async def fake_invoke(server_id, tool, args, **_kwargs):
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


# ── Tarea B: freshness annotation ──────────────────────────────────────────

def test_freshness_classification_thresholds(copilot_module):
    """The bucket boundaries: < 5 min fresh, < 1 h recent, < 24 h stale,
    >= 24 h very_stale, None unknown."""
    c = copilot_module._classify_freshness
    assert c(None) == "unknown"
    assert c("not a number") == "unknown"
    assert c(0) == "fresh"
    assert c(60) == "fresh"
    assert c(4 * 60) == "fresh"
    assert c(5 * 60) == "recent"        # boundary
    assert c(30 * 60) == "recent"
    assert c(59 * 60) == "recent"
    assert c(60 * 60) == "stale"        # boundary
    assert c(6 * 60 * 60) == "stale"
    assert c(24 * 60 * 60) == "very_stale"   # boundary
    assert c(48 * 60 * 60) == "very_stale"
    # Negative (clock skew) → fresh, not crash.
    assert c(-5) == "fresh"


def test_freshness_uses_age_seconds_already_on_citation(copilot_module):
    """If the citation already carries an age (set by the cartridge),
    no extra DB roundtrip — just classify."""
    citation = {"source": "sap_hcm", "entity": "Employee",
                "age_seconds": 30 * 60}   # 30 min
    out = _run(copilot_module._annotate_citation_freshness(citation))
    assert out["freshness_level"] == "recent"
    assert out["age_seconds"] == 30 * 60


def test_freshness_unknown_when_no_source_or_entity(copilot_module):
    out = _run(copilot_module._annotate_citation_freshness(
        {"source": "sap_hcm"}   # no entity
    ))
    assert out["freshness_level"] == "unknown"
    out = _run(copilot_module._annotate_citation_freshness({}))
    assert out["freshness_level"] == "unknown"


def test_freshness_unknown_when_freshness_lookup_raises(copilot_module, monkeypatch):
    """Swallow exceptions from freshness_for_cartridge_internal so the
    citation card still renders."""
    async def _boom(_):
        raise RuntimeError("DB down")
    import app.routers.freshness as fr
    monkeypatch.setattr(fr, "freshness_for_cartridge_internal", _boom)

    out = _run(copilot_module._annotate_citation_freshness(
        {"source": "sap_hcm", "entity": "Employee"}
    ))
    assert out["freshness_level"] == "unknown"


def test_freshness_lookup_resolves_entity_in_response(copilot_module, monkeypatch):
    """When the freshness service knows the entity, the citation
    inherits age_seconds + classification."""
    async def _ok(cartridge):
        return {
            "cartridge": cartridge,
            "entities": [
                {"entity": "Other",    "age_seconds": 9999},
                {"entity": "Employee", "age_seconds": 200},   # 200s → fresh
            ],
        }
    import app.routers.freshness as fr
    monkeypatch.setattr(fr, "freshness_for_cartridge_internal", _ok)

    out = _run(copilot_module._annotate_citation_freshness(
        {"source": "sap_hcm", "entity": "Employee"}
    ))
    assert out["age_seconds"] == 200
    assert out["freshness_level"] == "fresh"


def test_freshness_unknown_when_entity_not_in_response(copilot_module, monkeypatch):
    """The cartridge replied but the entity wasn't in the list."""
    async def _ok(cartridge):
        return {"cartridge": cartridge, "entities": []}
    import app.routers.freshness as fr
    monkeypatch.setattr(fr, "freshness_for_cartridge_internal", _ok)

    out = _run(copilot_module._annotate_citation_freshness(
        {"source": "replicon", "entity": "TimeEntry"}
    ))
    assert out["freshness_level"] == "unknown"


def test_freshness_router_internal_api_present():
    """v1.43 contract: the freshness router must export
    freshness_for_cartridge_internal so the copilot can call it
    without going through HTTP / auth."""
    from app.routers import freshness as fr
    assert hasattr(fr, "freshness_for_cartridge_internal")
    assert hasattr(fr, "freshness_all_internal")


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


# ── Tarea E: hallucination guardrails ──────────────────────────────────────

def test_response_with_numbers_no_citation_gets_warning(copilot_module):
    """When the LLM hedges a number but cited nothing, attach a warning."""
    f = copilot_module._check_for_hallucination
    assert f("Hay aproximadamente 1500 empleados.", has_citations=False)
    assert f("Around 200 records exist.",            has_citations=False)
    assert f("Debería ser 50 más o menos.",          has_citations=False)
    assert f("Estimamos en 80 usuarios activos.",    has_citations=False)
    assert f("Unos 30 DAGs corrieron hoy.",          has_citations=False)
    assert f("Rondan los 1500 empleados.",           has_citations=False)


def test_response_with_citation_no_warning(copilot_module):
    """If the LLM cited a tool, the numbers are grounded — no warning."""
    f = copilot_module._check_for_hallucination
    assert f("Hay aproximadamente 1500 empleados.", has_citations=True) is None


def test_response_with_no_numbers_no_warning(copilot_module):
    """Plain prose without hedged numbers → no warning."""
    f = copilot_module._check_for_hallucination
    assert f("No tengo ese dato. ¿Quieres que lo consulte?",
             has_citations=False) is None
    assert f("",     has_citations=False) is None
    assert f(None,   has_citations=False) is None


def test_concrete_number_without_hedging_is_not_flagged(copilot_module):
    """A bare 'hay 1247 empleados' (no hedging word) does NOT trigger.
    We only flag the hedging phrasing — bare assertions are the model's
    word to defend through citations, not ours to police via regex."""
    f = copilot_module._check_for_hallucination
    assert f("Hay 1247 empleados activos.", has_citations=False) is None
    assert f("Procesé 30 DAGs.",            has_citations=False) is None


def test_warning_prepended_to_reply_in_turn_payload(copilot_module):
    """End-to-end: when the LLM hedges without citations, the warning
    appears at the top of the reply the UI receives."""
    db = _FakeDB()
    _patch_full(copilot_module, db, tools=[],
                fake_chat=_make_chat_replying("Aproximadamente 1500 empleados."),
                fake_invoke=AsyncMock(return_value={}))
    admin = {"id": 99, "email": "x@example.com", "role": "admin"}
    conv = _run(copilot_module.create_conversation(user_id=admin["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="¿cuántos empleados?",
        user=admin,
    ))
    assert out["reply"].startswith("⚠️")
    assert "Aproximadamente 1500 empleados." in out["reply"]


# ── R1 DBA fixes ───────────────────────────────────────────────────────────

def test_oversize_citation_string_is_trimmed(copilot_module):
    """v1.43 R1-DBA F1: a hostile cartridge returning a 1MB run_id must
    not bloat conversation_messages.citations JSONB. Each string-shaped
    field is capped at MAX_CITATION_FIELD_CHARS."""
    cap = copilot_module._MAX_CITATION_FIELD_CHARS
    huge_run = "x" * (cap + 5000)
    cs = copilot_module._extract_citations(
        "foo",
        {"_meta": {"run_id": huge_run, "entity": "Y" * (cap + 1000),
                   "timestamp": "Z" * (cap + 1000), "row_count": 1}},
        "srv",
    )
    assert len(cs) == 1
    # Length is capped (and the elision char is appended).
    assert len(cs[0]["run_id"]) == cap
    assert cs[0]["run_id"].endswith("…")
    assert len(cs[0]["entity"]) == cap
    assert len(cs[0]["timestamp"]) == cap
    # Numeric field passes through.
    assert cs[0]["row_count"] == 1


def test_freshness_cache_avoids_n_plus_one(copilot_module, monkeypatch):
    """v1.43 R1-DBA F2: when many citations share a cartridge, the
    cache should reduce N lookups to one."""
    calls = []

    async def fake_lookup(cartridge):
        calls.append(cartridge)
        return {"cartridge": cartridge, "entities": [
            {"entity": "Employee",  "age_seconds": 100},
            {"entity": "TimeEntry", "age_seconds": 50},
        ]}

    import app.routers.freshness as fr
    monkeypatch.setattr(fr, "freshness_for_cartridge_internal", fake_lookup)

    cache: dict[str, dict] = {}
    citations = [
        {"source": "sap_hcm", "entity": "Employee"},
        {"source": "sap_hcm", "entity": "TimeEntry"},
        {"source": "sap_hcm", "entity": "Employee"},   # duplicate
    ]
    for c in citations:
        _run(copilot_module._annotate_citation_freshness(c, cache=cache))

    # All 3 citations share sap_hcm → only ONE backend call total.
    assert calls == ["sap_hcm"]
    # All got freshness assigned.
    for c in citations:
        assert c["freshness_level"] == "fresh"


def test_freshness_cache_negative_caches_failures(copilot_module, monkeypatch):
    """If the freshness lookup blows up, the cache stores an empty
    entries list so subsequent citations don't re-hit the failing
    backend within the same turn."""
    calls = []

    async def fake_lookup(cartridge):
        calls.append(cartridge)
        raise RuntimeError("DB down")

    import app.routers.freshness as fr
    monkeypatch.setattr(fr, "freshness_for_cartridge_internal", fake_lookup)

    cache: dict[str, dict] = {}
    for _ in range(5):
        _run(copilot_module._annotate_citation_freshness(
            {"source": "replicon", "entity": "Foo"}, cache=cache,
        ))
    # Only one call — the cache absorbed the rest.
    assert calls == ["replicon"]


def _make_chat_replying(text):
    """Tiny helper: a fake llm_client.chat that emits one assistant
    text block matching `text` and nothing else."""
    async def _chat(*, messages, **_kw):
        final = list(messages) + [
            {"role": "assistant",
             "content": [{"type": "text", "text": text}]},
        ]
        return (text, [], final)
    return _chat
