"""Sprint v1.42 — copilot_service unit tests.

Patches ``llm_client.chat``, ``mcp_registry`` and the asyncpg pool with
in-memory doubles to exercise the run-turn loop without standing up
the stack. The fake pool stores rows in dicts so ownership checks,
ordering and approval state can be observed.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


# ── Fake DB infrastructure ──────────────────────────────────────────────────

class FakeDB:
    """Tiny stand-in for the bits of asyncpg we use. Only the queries the
    copilot service actually executes are honoured — the test fails fast
    on an unknown query so the harness can't silently drift."""
    def __init__(self):
        self.conversations: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.audit_calls: list[dict] = []

    # asyncpg-compatible helpers ────────────────────────────────────────
    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, user_id, workspace_id, title, created_at, updated_at FROM conversations"):
            return self.conversations.get(args[0])
        if q.startswith("INSERT INTO conversations"):
            cid = str(uuid.uuid4())
            row = {
                "id": cid, "user_id": args[0], "workspace_id": args[1],
                "title": args[2], "created_at": "now", "updated_at": "now",
            }
            self.conversations[cid] = row
            return row
        if q.startswith("INSERT INTO conversation_messages"):
            mid = str(uuid.uuid4())
            self.messages.append({
                "id": mid,
                "conversation_id": args[0],
                "role": args[1],
                "content": args[2],
                "tool_calls": args[3],
                "tool_results": args[4],
                "model": args[5],
                "created_at": len(self.messages),
            })
            return {"id": mid}
        if q.startswith("SELECT tool_calls FROM conversation_messages"):
            for m in self.messages:
                if m["id"] == args[0] and m["conversation_id"] == args[1]:
                    return {"tool_calls": m["tool_calls"]}
            return None
        if q.startswith("UPDATE conversation_messages SET tool_results = jsonb_build_array"):
            # The atomic claim used by approve_pending_action: returns
            # tool_calls only if the row is still pending (tool_results
            # IS NULL). A second call returns None, which the service
            # turns into a 409.
            for m in self.messages:
                if (m["id"] == args[0]
                        and m["conversation_id"] == args[1]
                        and m["tool_calls"] is not None
                        and m["tool_results"] is None):
                    m["tool_results"] = '[{"approval_claim_id":"' + args[2] + '"}]'
                    return {"tool_calls": m["tool_calls"]}
            return None
        raise AssertionError(f"unmocked fetchrow: {q[:120]}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, title, created_at, updated_at FROM conversations WHERE user_id"):
            return [r for r in self.conversations.values() if r["user_id"] == args[0]]
        if q.startswith("SELECT role, content, tool_calls, tool_results FROM conversation_messages"):
            cid = args[0]
            msgs = [m for m in self.messages if m["conversation_id"] == cid]
            msgs.sort(key=lambda m: m["created_at"])
            return msgs
        if q.startswith("SELECT id, role, content, tool_calls, tool_results, created_at"):
            cid = args[0]
            return [m for m in self.messages if m["conversation_id"] == cid]
        raise AssertionError(f"unmocked fetch: {q[:120]}")

    async def execute(self, query: str, *args):
        return None


class FakePool:
    def __init__(self, db: FakeDB):
        self.db = db

    def acquire(self):
        return _Acquire(self.db)


class _Acquire:
    def __init__(self, db: FakeDB):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_):
        return False


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def admin_user():
    return {"id": 1, "email": "admin@example.com", "role": "admin"}


@pytest.fixture
def analyst_user():
    return {"id": 2, "email": "analyst@example.com", "role": "analyst"}


def _patch_pool(mod, db: FakeDB):
    mod.auth.pool = AsyncMock(return_value=FakePool(db))


def _patch_manifest(mod, tools: list[dict]):
    """tools shape: [{"name": "<server>___<bare>", "risk_level": "read"|...}]"""
    servers: dict[str, list] = {}
    for t in tools:
        srv, bare = t["name"].split("___", 1)
        servers.setdefault(srv, []).append({
            "name": bare,
            "description": t.get("description", ""),
            "input_schema": {"type": "object", "properties": {}},
            "risk_level": t.get("risk_level", "read"),
            "requires_approval": t.get("requires_approval", False),
        })
    mod.tool_manifest.build_manifest = AsyncMock(return_value={
        "version": "1.0", "servers": servers,
        "tool_count_total": sum(len(v) for v in servers.values()),
    })


def _patch_audit(mod, db: FakeDB):
    async def _record(**kw):
        db.audit_calls.append(kw)
    mod.audit_service.record_event = _record


def _patch_llm(mod, fake_chat):
    mod.llm_client.chat = fake_chat


def _patch_invoke(mod, fake_invoke):
    mod.mcp_registry.invoke = fake_invoke


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Tests ───────────────────────────────────────────────────────────────────

def test_copilot_creates_conversation(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    out = _run(copilot_module.create_conversation(
        user_id=admin_user["id"], workspace_id=None, title="Test convo",
    ))
    assert out["id"] in db.conversations
    assert out["title"] == "Test convo"
    assert out["user_id"] == 1


def test_copilot_read_tool_executes_immediately(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_list_dags", "risk_level": "read"},
    ])
    _patch_audit(copilot_module, db)
    invoked = []

    async def fake_invoke(server_id, tool, args):
        invoked.append((server_id, tool, args))
        return {"dags": ["sap_hcm_full", "replicon_users"]}

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, on_event=None):
        # Simulate the LLM calling one read tool then replying.
        result = await invoke_tool("infra", "airflow_list_dags", {})
        assert "dags" in result
        return ("Tenés 2 DAGs activos.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user_id=1))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"],
        user_message="¿Qué DAGs hay?",
        user=admin_user,
    ))
    assert invoked == [("infra", "airflow_list_dags", {})]
    assert out["requires_approval"] is False
    assert out["reply"] == "Tenés 2 DAGs activos."
    assert any(c["tool"] == "airflow_list_dags" for c in out["tool_calls"])


def test_copilot_destructive_tool_blocks_without_approval(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)

    invoked = []

    async def fake_invoke(*a, **kw):
        invoked.append(a)
        return {"deleted": True}

    captured = []

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, on_event=None):
        r = await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        captured.append(r)
        return ("Necesito tu aprobación.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user_id=1))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra el DAG x",
        user=admin_user,
    ))
    assert invoked == []  # mcp_registry was NOT called
    assert captured[0]["error"] == "approval_required"
    assert out["requires_approval"] is True
    assert len(out["pending_actions"]) == 1
    assert out["pending_actions"][0]["tool"] == "airflow_delete_dag"


def test_copilot_write_tool_blocks_without_approval(
    copilot_module, db, admin_user,
):
    """Non-read tools with requires_approval=True must not bypass the gate
    merely because their risk is "write" instead of "destructive"."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___foo_write_bar", "risk_level": "write", "requires_approval": True},
    ])
    _patch_audit(copilot_module, db)

    invoked = []

    async def fake_invoke(*a, **kw):
        invoked.append(a)
        return {"ok": True}

    captured = []

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, on_event=None):
        r = await invoke_tool("infra", "foo_write_bar", {"value": 1})
        captured.append(r)
        return ("Necesito aprobación.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user_id=1))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="actualiza algo",
        user=admin_user,
    ))
    assert invoked == []
    assert captured[0]["error"] == "approval_required"
    assert "(write)" in captured[0]["message"]
    assert out["requires_approval"] is True
    assert len(out["pending_actions"]) == 1
    assert out["pending_actions"][0]["tool"] == "foo_write_bar"


def test_copilot_destructive_tool_executes_with_approval(
    copilot_module, db, admin_user,
):
    """End-to-end approval flow. The fake LLM speaks the structured
    Anthropic message shape (assistant turn with a tool_use block,
    followed by the final reply) so the v1.42 refactor — which now
    persists from ``final_msgs`` instead of a side-log — gets a
    realistic chunk to work with."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)

    invoke_count = []

    async def fake_invoke(server_id, tool, args):
        invoke_count.append((server_id, tool, args))
        return {"deleted": True}

    async def fake_chat_first(*, messages, invoke_tool, **_kw):
        r = await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        # Build a realistic final_msgs: the history we received +
        # one assistant turn with a tool_use block + one user turn
        # with the tool_result + the final assistant text.
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "infra__airflow_delete_dag",
                 "input": {"dag_id": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": str(r)},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Espero aprobación."},
            ]},
        ]
        return ("Espero aprobación.", [], final)

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat_first)

    conv = _run(copilot_module.create_conversation(user_id=1))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra el DAG x",
        user=admin_user,
    ))
    assert out["requires_approval"] is True
    pending_msg_id = out["message_id"]

    async def fake_chat_second(*, messages, invoke_tool, **_kw):
        r = await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        assert r == {"deleted": True}
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_2",
                 "name": "infra__airflow_delete_dag",
                 "input": {"dag_id": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_2",
                 "content": str(r)},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Listo, eliminado."},
            ]},
        ]
        return ("Listo, eliminado.", [], final)

    _patch_llm(copilot_module, fake_chat_second)
    out2 = _run(copilot_module.approve_pending_action(
        conversation_id=conv["id"], message_id=pending_msg_id, user=admin_user,
    ))
    assert invoke_count == [("infra", "airflow_delete_dag", {"dag_id": "x"})]
    assert out2["requires_approval"] is False
    assert "eliminado" in out2["reply"].lower()


def test_copilot_audit_records_tool_call_with_risk_level(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_list_dags", "risk_level": "read"},
    ])
    _patch_audit(copilot_module, db)
    _patch_invoke(copilot_module, AsyncMock(return_value={"dags": []}))

    async def fake_chat(*, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_list_dags", {})
        return ("ok", [], [])

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=1))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="¿DAGs?",
        user=admin_user, ip="10.0.0.5", user_agent="pytest/1.0",
    ))
    # asyncio.create_task fires the audit; one event loop tick is enough.
    _run(asyncio.sleep(0))
    audit = db.audit_calls[-1]
    assert audit["tool_name"] == "airflow_list_dags"
    assert audit["risk_level"] == "read"
    assert audit["conversation_id"] == conv["id"]
    assert audit["ip"] == "10.0.0.5"
    assert audit["user_agent"] == "pytest/1.0"
    assert audit["status"] == "success"


def test_copilot_scrubs_secrets_from_tool_args(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___postgres_execute_query", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)
    _patch_invoke(copilot_module, AsyncMock(return_value={"rows_affected": 1}))

    async def fake_chat(*, invoke_tool, **_kw):
        await invoke_tool(
            "infra", "postgres_execute_query",
            {"sql": "UPDATE x SET y=$1", "password": "super-secret",
             "nested": {"api_key": "abc123"}},
        )
        return ("blocked", [], [])

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=1))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="run",
        user=admin_user,
    ))
    _run(asyncio.sleep(0))
    audit = db.audit_calls[-1]
    args = audit["tool_args"]
    assert args["password"] == "***"
    assert args["nested"]["api_key"] == "***"
    assert args["sql"] == "UPDATE x SET y=$1"  # untouched


def test_copilot_persists_messages_in_conversation_messages(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    async def fake_chat(*, messages, **_kw):
        text = "Hola, ¿en qué te ayudo?"
        final = list(messages) + [
            {"role": "assistant",
             "content": [{"type": "text", "text": text}]},
        ]
        return (text, [], final)

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=1))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="hola",
        user=admin_user,
    ))
    roles = [m["role"] for m in db.messages]
    assert roles == ["user", "assistant"]
    assert db.messages[0]["content"] == "hola"
    assert db.messages[1]["content"] == "Hola, ¿en qué te ayudo?"


def test_copilot_blocks_tool_when_user_lacks_permission(
    copilot_module, db, analyst_user,
):
    """Analyst only has copilot.use → can read, can't write/execute."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_set_variable", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)
    invoked = []
    async def fake_invoke(*a, **kw):
        invoked.append(a)
        return {}
    captured = []

    async def fake_chat(*, invoke_tool, **_kw):
        r = await invoke_tool("infra", "airflow_set_variable", {"k": "v"})
        captured.append(r)
        return ("denied", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    # Analyst owns this conversation.
    conv = _run(copilot_module.create_conversation(user_id=analyst_user["id"]))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="set k=v",
        user=analyst_user,
    ))
    assert invoked == []
    assert captured[0]["error"] == "permission_denied"
    assert captured[0]["required_permission"] == "copilot.execute"


def test_copilot_never_invents_data_when_tool_returns_empty(
    copilot_module, db, admin_user,
):
    """System prompt must instruct the LLM to say 'no encontré' on empty.
    We test the contract by verifying the prompt literal text."""
    assert "NUNCA inventes datos" in copilot_module.SYSTEM_PROMPT
    assert "No encontré ese dato" in copilot_module.SYSTEM_PROMPT


def test_copilot_user_message_size_limit(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    _patch_llm(copilot_module, AsyncMock(return_value=("ok", [], [])))
    conv = _run(copilot_module.create_conversation(user_id=1))
    huge = "x" * (copilot_module.MAX_USER_MESSAGE_CHARS + 1)
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id=conv["id"], user_message=huge, user=admin_user,
        ))
    assert "413" in str(exc_info.value) or "too long" in str(exc_info.value).lower()


def test_copilot_ownership_check_blocks_cross_user_access(
    copilot_module, db, admin_user, analyst_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    _patch_llm(copilot_module, AsyncMock(return_value=("ok", [], [])))
    # admin creates the convo, analyst tries to send to it
    conv = _run(copilot_module.create_conversation(user_id=admin_user["id"]))
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id=conv["id"], user_message="hola", user=analyst_user,
        ))
    assert "403" in str(exc_info.value) or "not your conversation" in str(exc_info.value).lower()


# ── R1 review fixes ────────────────────────────────────────────────────────

def test_copilot_tool_use_id_preserved_through_history(
    copilot_module, db, admin_user,
):
    """v1.42 R1 LLM-F1: the persisted tool_calls must keep the original
    Anthropic block id and tool_results must keep the matching
    tool_use_id. Without this, a follow-up turn's history reload
    generates fresh UUIDs and Anthropic API rejects with 400."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_list_dags", "risk_level": "read"},
    ])
    _patch_audit(copilot_module, db)
    _patch_invoke(copilot_module, AsyncMock(return_value={"dags": []}))

    async def fake_chat(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_list_dags", {})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_known_id",
                 "name": "infra__airflow_list_dags", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_known_id",
                 "content": "[]"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Sin DAGs."},
            ]},
        ]
        return ("Sin DAGs.", [], final)

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=1))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="dags?", user=admin_user,
    ))
    # Find the assistant message with tool_calls — its first call MUST
    # have id="toolu_known_id". Find the tool message — its first result
    # MUST have tool_use_id="toolu_known_id".
    assistant_msg = next(m for m in db.messages
                         if m["role"] == "assistant" and m["tool_calls"])
    tool_msg = next(m for m in db.messages
                    if m["role"] == "tool" and m["tool_results"])
    calls = json.loads(assistant_msg["tool_calls"])
    results = json.loads(tool_msg["tool_results"])
    assert calls[0]["id"] == "toolu_known_id"
    assert results[0]["tool_use_id"] == "toolu_known_id"


def test_copilot_unknown_risk_level_defaults_to_destructive(
    copilot_module, db, admin_user,
):
    """v1.42 R1 SEC-F2: a tool returning an unrecognised risk_level
    (typo, manifest drift) must be treated as destructive — never
    auto-execute as plain write."""
    _patch_pool(copilot_module, db)
    # Tool with a bogus risk_level.
    _patch_manifest(copilot_module, [
        {"name": "infra___mystery_tool", "risk_level": "mystery_value"},
    ])
    _patch_audit(copilot_module, db)
    invoked = []

    async def fake_invoke(*a, **_kw):
        invoked.append(a)
        return {}

    async def fake_chat(*, messages, invoke_tool, **_kw):
        r = await invoke_tool("infra", "mystery_tool", {"k": "v"})
        return ("blocked", [], list(messages))

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user_id=admin_user["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="run",
        user=admin_user,
    ))
    # mcp_registry NOT called → blocked by approval gate.
    assert invoked == []
    assert out["requires_approval"] is True


def test_copilot_502_does_not_leak_provider_exception(
    copilot_module, db, admin_user,
):
    """v1.42 R1 SEC-F4: the 502 raised when llm_client.chat() fails
    must NOT echo the SDK's str(exc) into the HTTP body. SDK errors
    sometimes include Authorization headers / API keys verbatim."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    leaky_secret = "Bearer sk-DO-NOT-LEAK-12345"

    async def fake_chat(**_kw):
        raise RuntimeError(
            "anthropic 401: Unauthorized — sent Authorization: " + leaky_secret
        )

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=admin_user["id"]))
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id=conv["id"], user_message="hola",
            user=admin_user,
        ))
    detail = str(exc_info.value)
    assert leaky_secret not in detail, (
        "502 leaked the raw provider exception into the HTTP body"
    )
    assert "502" in detail


def test_copilot_workspace_id_in_body_is_ignored_by_router():
    """v1.42 R1 SEC-F6: the router must NOT trust body.workspace_id —
    that would let a user attach a conversation to a workspace they
    don't belong to. The active workspace comes from the session."""
    src = (Path(__file__).resolve().parents[1] / "console" / "app"
           / "routers" / "copilot.py").read_text(encoding="utf-8")
    # Confirm by reading source: workspace_id is sourced from `user`,
    # not from body.
    assert "workspace_id = user.get(\"active_workspace_id\")" in src
    assert "body.get(\"workspace_id\")" not in src
    assert "(body or {}).get(\"workspace_id\")" not in src


def test_copilot_pending_actions_deduped(copilot_module, db, admin_user):
    """v1.42 R1 LLM-F4: if the LLM emits the same destructive call
    twice in one turn (it may, ignoring the prompt rule), we render
    one approval card, not two."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)
    _patch_invoke(copilot_module, AsyncMock(return_value={}))

    async def fake_chat(*, messages, invoke_tool, **_kw):
        # Same destructive call attempted twice.
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "X"})
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "X"})
        return ("aprobá", [], list(messages))

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=admin_user["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra X dos veces",
        user=admin_user,
    ))
    # One approval entry, not two.
    assert len(out["pending_actions"]) == 1


def test_copilot_approval_race_returns_409_on_second_call(
    copilot_module, db, admin_user,
):
    """v1.42 R1 SEC-F1: a concurrent second POST /approve must NOT
    execute the destructive action a second time. The atomic claim
    UPDATE returns 0 rows on the second call → 409."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)

    invoke_log = []

    async def fake_invoke(*a, **_kw):
        invoke_log.append(a)
        return {"deleted": True}

    async def fake_chat_first(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "infra__airflow_delete_dag", "input": {"dag_id": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": "approval_required"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Espero aprobación."},
            ]},
        ]
        return ("Espero aprobación.", [], final)

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat_first)
    conv = _run(copilot_module.create_conversation(user_id=admin_user["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="delete x", user=admin_user,
    ))
    pending_id = out["message_id"]

    # First approve wins, executes once.
    async def fake_chat_second(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        final = list(messages) + [
            {"role": "assistant",
             "content": [{"type": "text", "text": "Eliminado."}]},
        ]
        return ("Eliminado.", [], final)

    _patch_llm(copilot_module, fake_chat_second)
    out2 = _run(copilot_module.approve_pending_action(
        conversation_id=conv["id"], message_id=pending_id, user=admin_user,
    ))
    assert out2["reply"] == "Eliminado."
    assert len(invoke_log) == 1

    # Second concurrent approve must be rejected with 409.
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.approve_pending_action(
            conversation_id=conv["id"], message_id=pending_id, user=admin_user,
        ))
    assert "409" in str(exc_info.value)
    # And no extra invocation happened.
    assert len(invoke_log) == 1


def test_copilot_pending_action_args_are_scrubbed(
    copilot_module, db, admin_user,
):
    """v1.42 R1 SEC-F3: secrets in destructive args must be scrubbed
    before they hit the durable JSONB column."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_set_variable", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)
    _patch_invoke(copilot_module, AsyncMock(return_value={}))

    async def fake_chat(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_set_variable",
                          {"key": "k", "password": "PWN", "value": "v"})
        return ("aprobá", [], list(messages))

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user_id=admin_user["id"]))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="set k",
        user=admin_user,
    ))
    pa = out["pending_actions"][0]
    assert pa["args"]["password"] == "***"
    assert pa["args"]["key"] == "k"


# ── R2 review fixes ────────────────────────────────────────────────────────

def test_copilot_sanitises_error_string_for_audit(copilot_module):
    """v1.42 R2 SEC: upstream error strings (MCP server replies or
    invocation exceptions) sometimes echo credentials. Audit metadata
    must run them through the same redaction patterns logging_config
    uses before persistence."""
    out = copilot_module._sanitise_error(
        "POST https://x/y failed: Authorization: Bearer abc123def_ghi-jkl. password=hunter2"
    )
    assert "abc123def" not in out
    assert "hunter2" not in out
    assert "Bearer ***" in out
    assert "password=***" in out


def test_copilot_clips_oversized_tool_args(copilot_module):
    """v1.42 R2 DBA: the LLM can synthesise multi-MB tool_use input.
    Cap at MAX_TOOL_ARGS_BYTES before durable persistence."""
    huge = {"sql": "x" * (copilot_module.MAX_TOOL_ARGS_BYTES + 1000)}
    clipped = copilot_module._clip_tool_args(huge)
    assert clipped["_clipped"] is True
    assert clipped["_max_bytes"] == copilot_module.MAX_TOOL_ARGS_BYTES
    assert clipped["_original_bytes"] > copilot_module.MAX_TOOL_ARGS_BYTES
    # Small args pass through.
    small = {"k": "v"}
    assert copilot_module._clip_tool_args(small) == small


def test_copilot_clipped_input_is_swapped_for_empty_on_history_reload(copilot_module):
    """v1.42 R3 LLM-F1: a previously-clipped tool_use input must NOT
    be re-emitted to the LLM as ``{"_clipped": True, ...}`` — the
    model would either hallucinate those were real args or retry with
    the marker. _load_history swaps the clipped stub for ``{}``."""
    # We can drive _load_history through a tiny fake conn.
    class _C:
        async def fetch(self, *_a, **_kw):
            return [
                {
                    "role": "assistant",
                    "content": "I called the tool.",
                    "tool_calls": json.dumps([{
                        "id": "toolu_x", "name": "infra__do_thing",
                        "input": {"_clipped": True, "_original_bytes": 99999,
                                  "_preview": "huge..."},
                    }]),
                    "tool_results": None,
                },
            ]
    blocks = _run(copilot_module._load_history(_C(), "00000000-0000-0000-0000-000000000000"))
    assert blocks[0]["role"] == "assistant"
    tool_use_block = next(b for b in blocks[0]["content"] if b.get("type") == "tool_use")
    assert tool_use_block["input"] == {}, (
        "clipped marker must be hidden from the LLM on history reload"
    )


def test_copilot_rejects_invalid_uuid_with_400(copilot_module, db, admin_user):
    """v1.42 R2 DBA: invalid UUID path params used to bubble asyncpg
    InvalidTextRepresentationError as 500. Now caught at the boundary."""
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    _patch_llm(copilot_module, AsyncMock(return_value=("ok", [], [])))
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id="not-a-uuid",
            user_message="hola", user=admin_user,
        ))
    assert "400" in str(exc_info.value)
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.get_conversation_messages(
            conversation_id="also-not-a-uuid", user=admin_user,
        ))
    assert "400" in str(exc_info.value)
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.approve_pending_action(
            conversation_id="not-a-uuid", message_id="also-not",
            user=admin_user,
        ))
    assert "400" in str(exc_info.value)


def test_copilot_approval_key_is_args_specific(copilot_module):
    """The approval key must include args so approving 'delete X' does
    NOT also approve 'delete Y' that happened to share the bare name."""
    k1 = copilot_module._approval_key("airflow_delete_dag", {"dag_id": "X"})
    k2 = copilot_module._approval_key("airflow_delete_dag", {"dag_id": "Y"})
    assert k1 != k2
    # Order-insensitive: {a:1,b:2} == {b:2,a:1}.
    k3 = copilot_module._approval_key("foo", {"a": 1, "b": 2})
    k4 = copilot_module._approval_key("foo", {"b": 2, "a": 1})
    assert k3 == k4
