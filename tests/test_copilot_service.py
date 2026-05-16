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


def test_copilot_destructive_tool_executes_with_approval(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)

    invoke_count = []

    async def fake_invoke(server_id, tool, args):
        invoke_count.append((server_id, tool, args))
        return {"deleted": True}

    async def fake_chat_first(*, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        return ("Espero aprobación.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat_first)

    conv = _run(copilot_module.create_conversation(user_id=1))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra el DAG x",
        user=admin_user,
    ))
    assert out["requires_approval"] is True
    pending_msg_id = out["message_id"]

    async def fake_chat_second(*, invoke_tool, **_kw):
        r = await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        assert r == {"deleted": True}
        return ("Listo, eliminado.", [], [])

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

    async def fake_chat(**_kw):
        return ("Hola, ¿en qué te ayudo?", [], [])

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
