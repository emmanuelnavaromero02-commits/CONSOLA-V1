from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


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


class FakeDB:
    def __init__(self):
        self.conversations: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.audit_calls: list[dict] = []
        self.scope_calls: list[tuple[str, str]] = []
        self.current_tenant_id: str | None = None
        self.current_workspace_id: str | None = None

    def transaction(self):
        return _Transaction()

    def _has_scope(self) -> bool:
        return bool(self.current_workspace_id)

    def _conversation_visible(self, conversation_id: str) -> dict | None:
        row = self.conversations.get(str(conversation_id))
        if not row or not self._has_scope():
            return None
        if str(row.get("workspace_id")) != str(self.current_workspace_id):
            return None
        return row

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, user_id, workspace_id, title, created_at, updated_at FROM conversations"):
            return self._conversation_visible(str(args[0]))
        if q.startswith("INSERT INTO conversations"):
            if not self._has_scope() or str(args[1]) != str(self.current_workspace_id):
                return None
            cid = str(uuid.uuid4())
            row = {
                "id": cid, "user_id": args[0], "workspace_id": args[1],
                "title": args[2], "created_at": "now", "updated_at": "now",
            }
            self.conversations[cid] = row
            return row
        if q.startswith("INSERT INTO conversation_messages"):
            if not self._conversation_visible(str(args[0])):
                return None
            mid = str(uuid.uuid4())
            self.messages.append({
                "id": mid,
                "conversation_id": args[0],
                "role": args[1],
                "content": args[2],
                "tool_calls": args[3],
                "tool_results": args[4],
                "citations": args[5] if len(args) > 5 else None,
                "model": args[6] if len(args) > 6 else None,
                "created_at": len(self.messages),
            })
            return {"id": mid}
        if q.startswith("SELECT tool_calls FROM conversation_messages"):
            if not self._conversation_visible(str(args[1])):
                return None
            for m in self.messages:
                if (m["id"] == args[0]
                        and m["conversation_id"] == args[1]
                        and m["tool_calls"] is not None
                        and m["tool_results"] is None):
                    return {"tool_calls": m["tool_calls"]}
            return None
        if q.startswith("UPDATE conversation_messages SET tool_results = jsonb_build_array"):
            if not self._conversation_visible(str(args[1])):
                return None
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
            if not self._has_scope():
                return []
            return [
                r for r in self.conversations.values()
                if r["user_id"] == args[0]
                and str(r.get("workspace_id")) == str(self.current_workspace_id)
            ]
        if q.startswith("SELECT role, content, tool_calls, tool_results FROM conversation_messages"):
            cid = args[0]
            if not self._conversation_visible(str(cid)):
                return []
            msgs = [m for m in self.messages if m["conversation_id"] == cid]
            msgs.sort(key=lambda m: m["created_at"])
            return msgs
        if q.startswith("SELECT id, role, content, tool_calls, tool_results, citations, created_at"):
            cid = args[0]
            if not self._conversation_visible(str(cid)):
                return []
            return [m for m in self.messages if m["conversation_id"] == cid]
        raise AssertionError(f"unmocked fetch: {q[:120]}")

    async def execute(self, query: str, *args):
        if "set_config('app.tenant_id'" in query:
            self.current_tenant_id = str(args[0]) if args[0] is not None else None
            self.current_workspace_id = str(args[1])
            self.scope_calls.append((self.current_tenant_id or "", self.current_workspace_id))
            return None
        q = " ".join(query.split())
        if q.startswith("UPDATE conversations SET updated_at = NOW()"):
            if self._conversation_visible(str(args[0])):
                self.conversations[str(args[0])]["updated_at"] = "now"
            return None
        return None


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


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
    return {
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
    }


@pytest.fixture
def analyst_user():
    return {
        "id": 2,
        "email": "analyst@example.com",
        "role": "analyst",
        "active_tenant_id": "33333333-3333-3333-3333-333333333333",
        "active_workspace_id": "44444444-4444-4444-4444-444444444444",
    }


def _patch_pool(mod, db: FakeDB):
    mod.auth.pool = AsyncMock(return_value=FakePool(db))


def _patch_manifest(mod, tools: list[dict]):
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


def test_copilot_creates_conversation(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    out = _run(copilot_module.create_conversation(
        user=admin_user, title="Test convo",
    ))
    assert out["id"] in db.conversations
    assert out["title"] == "Test convo"
    assert out["user_id"] == 1
    assert db.scope_calls[-1] == (
        admin_user["active_tenant_id"],
        admin_user["active_workspace_id"],
    )


def test_copilot_conversation_list_is_scoped_by_active_workspace(
    copilot_module, db, admin_user, analyst_user,
):
    _patch_pool(copilot_module, db)

    a_conv = _run(copilot_module.create_conversation(
        user=admin_user, title="Tenant A",
    ))
    b_conv = _run(copilot_module.create_conversation(
        user=analyst_user, title="Tenant B",
    ))

    a_rows = _run(copilot_module.list_conversations(user=admin_user))
    b_rows = _run(copilot_module.list_conversations(user=analyst_user))

    assert [row["id"] for row in a_rows["conversations"]] == [a_conv["id"]]
    assert [row["id"] for row in b_rows["conversations"]] == [b_conv["id"]]
    assert a_conv["id"] not in [row["id"] for row in b_rows["conversations"]]
    assert b_conv["id"] not in [row["id"] for row in a_rows["conversations"]]


def test_copilot_messages_are_scoped_by_parent_conversation_workspace(
    copilot_module, db, admin_user, analyst_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    async def fake_chat(*, messages, **_kw):
        text = "ok"
        return (text, [], list(messages) + [{
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        }])

    _patch_llm(copilot_module, fake_chat)

    a_conv = _run(copilot_module.create_conversation(user=admin_user))
    b_conv = _run(copilot_module.create_conversation(user=analyst_user))
    _run(copilot_module.run_turn(
        conversation_id=a_conv["id"],
        user_message="hola A",
        user=admin_user,
    ))
    _run(copilot_module.run_turn(
        conversation_id=b_conv["id"],
        user_message="hola B",
        user=analyst_user,
    ))

    a_messages = _run(copilot_module.get_conversation_messages(
        conversation_id=a_conv["id"],
        user=admin_user,
    ))
    assert [msg["content"] for msg in a_messages["messages"]][:2] == ["hola A", "ok"]

    with pytest.raises(HTTPException) as cross:
        _run(copilot_module.get_conversation_messages(
            conversation_id=b_conv["id"],
            user=admin_user,
        ))
    assert cross.value.status_code == 404


def test_copilot_conversations_fail_closed_without_active_workspace(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    user_without_scope = {
        key: value
        for key, value in admin_user.items()
        if key not in {"active_tenant_id", "active_workspace_id"}
    }

    with pytest.raises(HTTPException) as create_exc:
        _run(copilot_module.create_conversation(user=user_without_scope))
    assert create_exc.value.status_code == 403

    with pytest.raises(HTTPException) as list_exc:
        _run(copilot_module.list_conversations(user=user_without_scope))
    assert list_exc.value.status_code == 403


def test_copilot_read_tool_executes_immediately(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_list_dags", "risk_level": "read"},
    ])
    _patch_audit(copilot_module, db)
    invoked = []

    async def fake_invoke(server_id, tool, args, **_kwargs):
        invoked.append((server_id, tool, args))
        return {"dags": ["sap_hcm_full", "replicon_users"]}

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, on_event=None, **_kw):
        result = await invoke_tool("infra", "airflow_list_dags", {})
        assert "dags" in result
        return ("Tenés 2 DAGs activos.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user=admin_user))
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

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, on_event=None, **_kw):
        r = await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
        captured.append(r)
        return ("Necesito tu aprobación.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra el DAG x",
        user=admin_user,
    ))
    assert invoked == []
    assert captured[0]["error"] == "approval_required"
    assert out["requires_approval"] is True
    assert len(out["pending_actions"]) == 1
    assert out["pending_actions"][0]["tool"] == "airflow_delete_dag"


def test_copilot_write_tool_blocks_without_approval(
    copilot_module, db, admin_user,
):
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

    async def fake_chat(*, system, messages, tools, invoke_tool, tool_server_map, on_event=None, **_kw):
        r = await invoke_tool("infra", "foo_write_bar", {"value": 1})
        captured.append(r)
        return ("Necesito aprobación.", [], [])

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat)

    conv = _run(copilot_module.create_conversation(user=admin_user))
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


def test_copilot_write_tool_executes_after_approval(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___foo_write_bar", "risk_level": "write", "requires_approval": True},
    ])
    _patch_audit(copilot_module, db)

    invoke_count = []

    async def fake_invoke(server_id, tool, args, **_kwargs):
        invoke_count.append((server_id, tool, args))
        return {"ok": True}

    async def fake_chat_first(*, messages, invoke_tool, **_kw):
        r = await invoke_tool("infra", "foo_write_bar", {"value": 1})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "infra__foo_write_bar", "input": {"value": 1}},
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

    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="actualiza algo",
        user=admin_user,
    ))
    assert out["requires_approval"] is True
    pending_msg_id = out["message_id"]
    assert invoke_count == []

    async def fake_chat_second(*, messages, invoke_tool, **_kw):
        assert any(
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and "Aprobé la ejecución" in m["content"]
            for m in messages
        )
        assert any(
            m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(
                b.get("type") == "tool_result" and '"ok": true' in str(b.get("content"))
                for b in m["content"]
            )
            for m in messages
        )
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Listo."},
            ]},
        ]
        return ("Listo.", [], final)

    _patch_llm(copilot_module, fake_chat_second)
    out2 = _run(copilot_module.approve_pending_action(
        conversation_id=conv["id"], message_id=pending_msg_id, user=admin_user,
    ))
    assert invoke_count == [("infra", "foo_write_bar", {"value": 1})]
    assert out2["requires_approval"] is False


def test_copilot_write_approval_does_not_require_execute_permission(
    copilot_module, db, monkeypatch,
):
    writer_user = {
        "id": 3,
        "email": "writer@example.com",
        "role": "custom",
        "active_tenant_id": "55555555-5555-5555-5555-555555555555",
        "active_workspace_id": "66666666-6666-6666-6666-666666666666",
    }
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___foo_write_bar", "risk_level": "write", "requires_approval": True},
    ])
    _patch_audit(copilot_module, db)
    monkeypatch.setattr(
        copilot_module.permissions,
        "has_permission",
        lambda _user, permission: permission in {"copilot.use", "copilot.write"},
    )

    invoke_count = []

    async def fake_invoke(server_id, tool, args, **_kwargs):
        invoke_count.append((server_id, tool, args))
        return {"ok": True}

    async def fake_chat_first(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "foo_write_bar", {"value": 7})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "infra__foo_write_bar", "input": {"value": 7}},
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

    async def fake_chat_second(*, messages, **_kw):
        final = list(messages) + [
            {"role": "assistant",
             "content": [{"type": "text", "text": "Listo."}]},
        ]
        return ("Listo.", [], final)

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat_first)
    conv = _run(copilot_module.create_conversation(user=writer_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="actualiza algo", user=writer_user,
    ))

    _patch_llm(copilot_module, fake_chat_second)
    _run(copilot_module.approve_pending_action(
        conversation_id=conv["id"], message_id=out["message_id"], user=writer_user,
    ))
    assert invoke_count == [("infra", "foo_write_bar", {"value": 7})]


def test_copilot_destructive_tool_executes_with_approval(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)

    invoke_count = []

    async def fake_invoke(server_id, tool, args, **_kwargs):
        invoke_count.append((server_id, tool, args))
        return {"deleted": True}

    async def fake_chat_first(*, messages, invoke_tool, **_kw):
        r = await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "x"})
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

    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra el DAG x",
        user=admin_user,
    ))
    assert out["requires_approval"] is True
    pending_msg_id = out["message_id"]

    async def fake_chat_second(*, messages, invoke_tool, **_kw):
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Listo, eliminado."},
            ]},
        ]
        assert any(
            m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(
                b.get("type") == "tool_result"
                and '"deleted": true' in str(b.get("content"))
                for b in m["content"]
            )
            for m in messages
        )
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
    conv = _run(copilot_module.create_conversation(user=admin_user))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="¿DAGs?",
        user=admin_user, ip="10.0.0.5", user_agent="pytest/1.0",
    ))
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
    conv = _run(copilot_module.create_conversation(user=admin_user))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="run",
        user=admin_user,
    ))
    _run(asyncio.sleep(0))
    audit = db.audit_calls[-1]
    args = audit["tool_args"]
    assert args["password"] == "***"
    assert args["nested"]["api_key"] == "***"
    assert args["sql"] == "UPDATE x SET y=$1"


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
    conv = _run(copilot_module.create_conversation(user=admin_user))
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

    conv = _run(copilot_module.create_conversation(user=analyst_user))
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
    assert "NUNCA inventes datos" in copilot_module.SYSTEM_PROMPT
    assert "No encontré ese dato" in copilot_module.SYSTEM_PROMPT


def test_copilot_user_message_size_limit(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)
    _patch_llm(copilot_module, AsyncMock(return_value=("ok", [], [])))
    conv = _run(copilot_module.create_conversation(user=admin_user))
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
    conv = _run(copilot_module.create_conversation(user=admin_user))
    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id=conv["id"], user_message="hola", user=analyst_user,
        ))
    assert (
        "403" in str(exc_info.value)
        or "404" in str(exc_info.value)
        or "not your conversation" in str(exc_info.value).lower()
    )


def test_copilot_tool_use_id_preserved_through_history(
    copilot_module, db, admin_user,
):
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
    conv = _run(copilot_module.create_conversation(user=admin_user))
    _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="dags?", user=admin_user,
    ))
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
    _patch_pool(copilot_module, db)
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

    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="run",
        user=admin_user,
    ))
    assert invoked == []
    assert out["requires_approval"] is True


def test_copilot_502_does_not_leak_provider_exception(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    leaky_secret = "Bearer sk-DO-NOT-LEAK-12345"

    async def fake_chat(**_kw):
        raise RuntimeError(
            "anthropic 401: Unauthorized — sent Authorization: " + leaky_secret
        )

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user=admin_user))
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
    src = (Path(__file__).resolve().parents[1] / "console" / "app"
           / "routers" / "copilot.py").read_text(encoding="utf-8")
    assert "copilot_service.create_conversation(" in src
    assert "user=user" in src
    assert "body.get(\"workspace_id\")" not in src
    assert "(body or {}).get(\"workspace_id\")" not in src


def test_copilot_pending_actions_deduped(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___airflow_delete_dag", "risk_level": "destructive"},
    ])
    _patch_audit(copilot_module, db)
    _patch_invoke(copilot_module, AsyncMock(return_value={}))

    async def fake_chat(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "X"})
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "X"})
        return ("aprobá", [], list(messages))

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="borra X dos veces",
        user=admin_user,
    ))
    assert len(out["pending_actions"]) == 1


def test_copilot_approval_race_returns_409_on_second_call(
    copilot_module, db, admin_user,
):
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
    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="delete x", user=admin_user,
    ))
    pending_id = out["message_id"]

    async def fake_chat_second(*, messages, invoke_tool, **_kw):
        assert any(
            m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(
                b.get("type") == "tool_result"
                and '"deleted": true' in str(b.get("content"))
                for b in m["content"]
            )
            for m in messages
        )
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

    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.approve_pending_action(
            conversation_id=conv["id"], message_id=pending_id, user=admin_user,
        ))
    assert "409" in str(exc_info.value)
    assert len(invoke_log) == 1


def test_copilot_approval_dedupes_duplicate_tool_calls(
    copilot_module, db, admin_user,
):
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
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "dupe"})
        await invoke_tool("infra", "airflow_delete_dag", {"dag_id": "dupe"})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "infra__airflow_delete_dag",
                 "input": {"dag_id": "dupe"}},
                {"type": "tool_use", "id": "toolu_2",
                 "name": "infra__airflow_delete_dag",
                 "input": {"dag_id": "dupe"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": "approval_required"},
                {"type": "tool_result", "tool_use_id": "toolu_2",
                 "content": "approval_required"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Espero aprobación."},
            ]},
        ]
        return ("Espero aprobación.", [], final)

    async def fake_chat_second(*, messages, **_kw):
        final = list(messages) + [
            {"role": "assistant",
             "content": [{"type": "text", "text": "Eliminado una vez."}]},
        ]
        return ("Eliminado una vez.", [], final)

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat_first)
    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"],
        user_message="borra dupe dos veces",
        user=admin_user,
    ))

    _patch_llm(copilot_module, fake_chat_second)
    _run(copilot_module.approve_pending_action(
        conversation_id=conv["id"], message_id=out["message_id"], user=admin_user,
    ))
    assert invoke_log == [("infra", "airflow_delete_dag", {"dag_id": "dupe"})]


def test_copilot_approved_tool_result_is_clipped_before_history(
    copilot_module, db, admin_user, monkeypatch,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [
        {"name": "infra___foo_write_bar", "risk_level": "write", "requires_approval": True},
    ])
    _patch_audit(copilot_module, db)
    monkeypatch.setattr(copilot_module.llm_client, "_MAX_TOOL_RESULT_BYTES", 100)

    async def fake_invoke(*_a, **_kw):
        return {"rows": [{"value": "x" * 80} for _ in range(20)], "count": 20}

    async def fake_chat_first(*, messages, invoke_tool, **_kw):
        await invoke_tool("infra", "foo_write_bar", {"value": 1})
        final = list(messages) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "infra__foo_write_bar", "input": {"value": 1}},
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

    seen_tool_result = []

    async def fake_chat_second(*, messages, **_kw):
        for m in messages:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        seen_tool_result.append(str(b.get("content")))
        final = list(messages) + [
            {"role": "assistant",
             "content": [{"type": "text", "text": "Listo."}]},
        ]
        return ("Listo.", [], final)

    _patch_invoke(copilot_module, fake_invoke)
    _patch_llm(copilot_module, fake_chat_first)
    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="write big", user=admin_user,
    ))

    _patch_llm(copilot_module, fake_chat_second)
    _run(copilot_module.approve_pending_action(
        conversation_id=conv["id"], message_id=out["message_id"], user=admin_user,
    ))
    assert any("_truncated" in item for item in seen_tool_result)


def test_copilot_pending_action_args_are_scrubbed(
    copilot_module, db, admin_user,
):
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
    conv = _run(copilot_module.create_conversation(user=admin_user))
    out = _run(copilot_module.run_turn(
        conversation_id=conv["id"], user_message="set k",
        user=admin_user,
    ))
    pa = out["pending_actions"][0]
    assert pa["args"]["password"] == "***"
    assert pa["args"]["key"] == "k"


def test_copilot_sanitises_error_string_for_audit(copilot_module):
    out = copilot_module._sanitise_error(
        "POST https://x/y failed: Authorization: Bearer abc123def_ghi-jkl. password=hunter2"
    )
    assert "abc123def" not in out
    assert "hunter2" not in out
    assert "Bearer ***" in out
    assert "password=***" in out


def test_copilot_sanitises_basic_and_apikey_authorization_errors(copilot_module):
    out = copilot_module._sanitise_error(
        "GET failed Authorization: Basic dXNlcjpwYXNz authorization=ApiKey live-secret"
    )
    assert "dXNlcjpwYXNz" not in out
    assert "live-secret" not in out
    assert out.count("authorization=***") == 2


def test_copilot_clips_oversized_tool_args(copilot_module):
    huge = {"sql": "x" * (copilot_module.MAX_TOOL_ARGS_BYTES + 1000)}
    clipped = copilot_module._clip_tool_args(huge)
    assert clipped["_clipped"] is True
    assert clipped["_max_bytes"] == copilot_module.MAX_TOOL_ARGS_BYTES
    assert clipped["_original_bytes"] > copilot_module.MAX_TOOL_ARGS_BYTES
    small = {"k": "v"}
    assert copilot_module._clip_tool_args(small) == small


def test_copilot_clipped_input_is_swapped_for_empty_on_history_reload(copilot_module):
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


def test_copilot_surfaces_sanitised_llm_provider_reason(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    async def fake_chat(**_kwargs):
        raise copilot_module.llm_client.LLMProviderError(
            "Anthropic billing or credit limit reached; add credits before using the live copilot"
        )

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user=admin_user))

    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id=conv["id"],
            user_message="hola",
            user=admin_user,
        ))

    assert "Anthropic billing or credit limit reached" in str(exc_info.value)
    assistant_messages = [m for m in db.messages if m["role"] == "assistant"]
    assert assistant_messages
    assert "Anthropic billing or credit limit reached" in assistant_messages[-1]["content"]
    assert "sk-" not in assistant_messages[-1]["content"]


def test_copilot_surfaces_sanitised_llm_provider_authorization_headers(copilot_module, db, admin_user):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    async def fake_chat(**_kwargs):
        raise copilot_module.llm_client.LLMProviderError(
            "upstream 401 Authorization: Basic dXNlcjpwYXNz authorization=ApiKey live-secret"
        )

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user=admin_user))

    with pytest.raises(Exception) as exc_info:
        _run(copilot_module.run_turn(
            conversation_id=conv["id"],
            user_message="hola",
            user=admin_user,
        ))

    assert "dXNlcjpwYXNz" not in str(exc_info.value)
    assert "live-secret" not in str(exc_info.value)
    assistant_messages = [m for m in db.messages if m["role"] == "assistant"]
    assert assistant_messages
    assert "dXNlcjpwYXNz" not in assistant_messages[-1]["content"]
    assert "live-secret" not in assistant_messages[-1]["content"]
    assert "authorization=***" in assistant_messages[-1]["content"]


def test_copilot_approval_key_is_args_specific(copilot_module):
    k1 = copilot_module._approval_key("infra", "airflow_delete_dag", {"dag_id": "X"})
    k2 = copilot_module._approval_key("infra", "airflow_delete_dag", {"dag_id": "Y"})
    assert k1 != k2
    k_other_server = copilot_module._approval_key("sap", "airflow_delete_dag", {"dag_id": "X"})
    assert k1 != k_other_server
    k3 = copilot_module._approval_key("infra", "foo", {"a": 1, "b": 2})
    k4 = copilot_module._approval_key("infra", "foo", {"b": 2, "a": 1})
    assert k3 == k4


def test_copilot_open_turn_stream_emits_events_and_scrubs_args(
    copilot_module, db, admin_user,
):
    _patch_pool(copilot_module, db)
    _patch_manifest(copilot_module, [])
    _patch_audit(copilot_module, db)

    async def fake_chat(*, messages, on_event=None, **_kw):
        assert on_event is not None
        await on_event({"type": "text_delta", "text": "Ho"})
        await on_event({
            "type": "tool_use",
            "tool": "probe",
            "server": "infra",
            "args": {"password": "PWN", "safe": "ok"},
        })
        final = list(messages) + [
            {"role": "assistant", "content": [{"type": "text", "text": "Hola"}]},
        ]
        return ("Hola", [], final)

    _patch_llm(copilot_module, fake_chat)
    conv = _run(copilot_module.create_conversation(user=admin_user))

    async def collect():
        stream = await copilot_module.open_turn_stream(
            conversation_id=conv["id"],
            user_message="hola",
            user=admin_user,
        )
        out = []
        async for evt in stream:
            out.append(evt)
        return out

    events = _run(collect())
    assert events[0]["type"] == "ready"
    assert {"type": "text_delta", "text": "Ho"} in events
    tool_evt = next(e for e in events if e.get("type") == "tool_use")
    assert tool_evt["args"] == {"password": "***", "safe": "ok"}
    done_evt = next(e for e in events if e.get("type") == "done")
    assert done_evt["result"]["reply"] == "Hola"
