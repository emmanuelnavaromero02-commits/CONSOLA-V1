from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99t_external_action_framework.sql"


@pytest.fixture()
def external_actions(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    from app.services import external_actions as mod

    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    monkeypatch.setenv("EXTERNAL_ACTION_SANDBOX_ENABLED", "true")
    return mod


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Acquire:
    def __init__(self, db: "FakeActionDB"):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class FakePool:
    def __init__(self, db: "FakeActionDB"):
        self.db = db

    def acquire(self):
        return _Acquire(self.db)


class FakeActionDB:
    def __init__(self):
        self.actions: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.idempotency: dict[tuple[str, str], dict[str, Any]] = {}
        self.scope_calls: list[tuple[str | None, str]] = []
        self.current_tenant_id: str | None = None
        self.current_workspace_id: str | None = None

    def transaction(self):
        return _Tx()

    def _visible(self, row: dict[str, Any] | None, workspace_id: str | None = None) -> bool:
        if not row or not self.current_workspace_id:
            return False
        expected_workspace = workspace_id or self.current_workspace_id
        return (
            str(row["workspace_id"]) == str(expected_workspace)
            and str(row["workspace_id"]) == str(self.current_workspace_id)
            and str(row.get("tenant_id") or "") == str(self.current_tenant_id or "")
        )

    async def execute(self, query: str, *args):
        if "set_config('app.tenant_id'" in query:
            self.current_tenant_id = str(args[0]) if args[0] else None
            self.current_workspace_id = str(args[1])
            self.scope_calls.append((self.current_tenant_id, self.current_workspace_id))
            return None
        q = " ".join(query.split())
        if q.startswith("INSERT INTO external_action_events"):
            self.events.append(
                {
                    "id": len(self.events) + 1,
                    "tenant_id": args[0],
                    "workspace_id": args[1],
                    "action_id": args[2],
                    "event_type": args[3],
                    "status": args[4],
                    "actor_id": args[5],
                    "actor_email": args[6],
                    "metadata": args[7],
                    "created_at": datetime.now(UTC),
                }
            )
            return None
        if q.startswith("INSERT INTO external_action_idempotency_keys"):
            tenant_id, workspace_id, action_id, operation, key, response = args[:6]
            existing = self.idempotency.get((str(workspace_id), str(key)))
            if existing and (
                str(existing.get("action_id")) != str(action_id)
                or str(existing.get("operation")) != str(operation)
            ):
                return None
            self.idempotency[(str(workspace_id), str(key))] = {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "action_id": action_id,
                "operation": operation,
                "idempotency_key": key,
                "response": response,
                "status": "completed",
            }
            return None
        if q.startswith("UPDATE external_actions SET status = 'executing'"):
            row = self.actions.get(str(args[0]))
            if self._visible(row, str(args[1])):
                row["status"] = "executing"
                row["updated_at"] = datetime.now(UTC)
            return None
        if q.startswith("UPDATE external_actions SET status = 'expired'"):
            row = self.actions.get(str(args[0]))
            if self._visible(row, str(args[1])):
                row["status"] = "expired"
                row["completed_at"] = row.get("completed_at") or datetime.now(UTC)
            return None
        raise AssertionError(f"unmocked execute: {q[:180]}")

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT action_id, operation, response, status"):
            return self.idempotency.get((str(args[0]), str(args[1])))
        if q.startswith("INSERT INTO external_actions"):
            tenant_id, workspace_id = args[0], args[1]
            if str(workspace_id) != str(self.current_workspace_id):
                return None
            action_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            row = {
                "id": action_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "source_type": args[2],
                "source_id": args[3],
                "action_type": args[4],
                "adapter_name": args[5],
                "payload": args[6],
                "dry_run_payload": args[7],
                "dry_run_result": "{}",
                "execution_result": "{}",
                "idempotency_key": args[8],
                "status": "pending_approval",
                "created_by": args[9],
                "approved_by": None,
                "rejected_by": None,
                "cancelled_by": None,
                "expires_at": args[10],
                "metadata": args[11],
                "created_at": now,
                "updated_at": now,
                "approved_at": None,
                "rejected_at": None,
                "cancelled_at": None,
                "completed_at": None,
            }
            self.actions[action_id] = row
            return row
        if q.startswith("SELECT * FROM external_actions WHERE id"):
            row = self.actions.get(str(args[0]))
            return row if self._visible(row, str(args[1])) else None
        if q.startswith("UPDATE external_actions SET status = 'dry_run_ready'"):
            row = self.actions.get(str(args[0]))
            if not self._visible(row, str(args[1])):
                return None
            row.update(
                {
                    "status": "dry_run_ready",
                    "dry_run_payload": args[2],
                    "dry_run_result": args[3],
                    "updated_at": datetime.now(UTC),
                }
            )
            return row
        if q.startswith("UPDATE external_actions SET status = 'approved'"):
            row = self.actions.get(str(args[0]))
            if not self._visible(row, str(args[1])):
                return None
            row.update(
                {
                    "status": "approved",
                    "approved_by": args[2],
                    "approved_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )
            return row
        if q.startswith("UPDATE external_actions SET status = $3, execution_result"):
            row = self.actions.get(str(args[0]))
            if not self._visible(row, str(args[1])):
                return None
            row.update(
                {
                    "status": args[2],
                    "execution_result": args[3],
                    "updated_at": datetime.now(UTC),
                    "completed_at": datetime.now(UTC),
                }
            )
            return row
        if q.startswith("UPDATE external_actions SET status = $3"):
            row = self.actions.get(str(args[0]))
            if not self._visible(row, str(args[1])):
                return None
            row.update(
                {
                    "status": args[2],
                    "updated_at": datetime.now(UTC),
                    "completed_at": datetime.now(UTC),
                }
            )
            return row
        raise AssertionError(f"unmocked fetchrow: {q[:180]}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT * FROM external_actions"):
            workspace_id = str(args[0])
            rows = [
                row
                for row in self.actions.values()
                if self._visible(row, workspace_id)
            ]
            return rows[: int(args[1])]
        if q.startswith("SELECT event_type, status"):
            action_id, workspace_id = str(args[0]), str(args[1])
            return [
                event
                for event in self.events
                if str(event["action_id"]) == action_id
                and str(event["workspace_id"]) == workspace_id
                and workspace_id == str(self.current_workspace_id)
            ]
        raise AssertionError(f"unmocked fetch: {q[:180]}")


def _user(
    user_id: int,
    *,
    role: str = "workspace_admin",
    tenant_id: str = "11111111-1111-1111-1111-111111111111",
    workspace_id: str = "22222222-2222-2222-2222-222222222222",
) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"user{user_id}@example.com",
        "role": role,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


def _patch_pool(mod, monkeypatch, db: FakeActionDB) -> None:
    monkeypatch.setattr(mod.auth, "pool", AsyncMock(return_value=FakePool(db)))

    async def audit(**_kwargs):
        return None

    monkeypatch.setattr(mod.audit_service, "record_event", audit)


def test_external_action_migration_is_scoped_and_append_only():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert "BYPASSRLS" not in sql
    for table in (
        "external_actions",
        "external_action_events",
        "external_action_idempotency_keys",
    ):
        assert f"ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY" in sql
        assert table in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "prevent_external_action_event_mutation" in sql
    assert "BEFORE UPDATE OR DELETE ON external_action_events" in sql
    assert "GRANT SELECT, INSERT ON external_action_events TO omega_console" in sql
    assert "adapter_name" in sql and "TEXT NOT NULL DEFAULT 'sandbox'" in sql


def test_actions_router_requires_auth_csrf_and_expected_permissions():
    router = (REPO / "console" / "app" / "routers" / "actions.py").read_text(encoding="utf-8")
    main = (REPO / "console" / "app" / "main.py").read_text(encoding="utf-8")

    assert 'APIRouter(prefix="/api/actions"' in router
    assert "require_authenticated" in router
    assert "require_csrf" in router
    assert 'require_permission("control_room.write")' in router
    assert 'require_permission("control_room.execute")' in router
    assert "external_actions.execute(user, action_id" in router
    assert "include_router(actions_router.router)" in main


def test_payload_safety_rejects_secrets_and_redacts_audit_values(external_actions):
    assert external_actions.validate_payload({"safe": {"count": 1}}) == {"safe": {"count": 1}}
    assert external_actions.redact_sensitive({"api_key": "abc", "nested": {"token": "def"}}) == {
        "api_key": "<redacted>",
        "nested": {"token": "<redacted>"},
    }
    with pytest.raises(HTTPException) as secret_exc:
        external_actions.validate_payload({"credentials": "do-not-store"})
    assert secret_exc.value.status_code == 400
    with pytest.raises(HTTPException) as depth_exc:
        external_actions.validate_payload({"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}})
    assert depth_exc.value.status_code == 400
    with pytest.raises(HTTPException) as list_exc:
        external_actions.validate_payload({"items": list(range(external_actions.MAX_LIST_ITEMS + 1))})
    assert list_exc.value.status_code == 400
    with pytest.raises(HTTPException) as adapter_exc:
        external_actions._ensure_adapter_allowed("real_writeback_adapter")
    assert adapter_exc.value.status_code == 409


@pytest.mark.asyncio
async def test_external_action_lifecycle_is_scoped_maker_checker_and_idempotent(
    external_actions,
    monkeypatch,
):
    db = FakeActionDB()
    _patch_pool(external_actions, monkeypatch, db)
    creator = _user(10, role="workspace_admin")
    checker = _user(11, role="workspace_admin")
    tenant_b = _user(
        12,
        role="workspace_admin",
        tenant_id="33333333-3333-3333-3333-333333333333",
        workspace_id="44444444-4444-4444-4444-444444444444",
    )

    proposed = await external_actions.propose(
        creator,
        {
            "source_type": "control_room",
            "source_id": "item-1",
            "action_type": "sandbox_notify",
            "adapter_name": "sandbox",
            "payload": {"sandbox_outcome": "success", "message": "hello"},
            "idempotency_key": "proposal-key",
        },
    )
    action_id = proposed["action"]["id"]
    assert proposed["action"]["status"] == "pending_approval"
    assert proposed["action"]["payload"] == {"sandbox_outcome": "success", "message": "hello"}
    assert db.scope_calls[-1] == (creator["active_tenant_id"], creator["active_workspace_id"])

    replay = await external_actions.propose(
        creator,
        {
            "source_type": "control_room",
            "source_id": "item-1",
            "action_type": "sandbox_notify",
            "adapter_name": "sandbox",
            "payload": {"sandbox_outcome": "success", "message": "hello"},
            "idempotency_key": "proposal-key",
        },
    )
    assert replay["action"]["id"] == action_id
    assert len(db.actions) == 1

    assert await external_actions.list_actions(tenant_b) == {"actions": []}
    with pytest.raises(HTTPException) as hidden:
        await external_actions.get_action(tenant_b, action_id)
    assert hidden.value.status_code == 404

    dry_run = await external_actions.dry_run(
        creator,
        action_id,
        {"idempotency_key": "dry-run-key"},
    )
    assert dry_run["action"]["status"] == "dry_run_ready"
    assert dry_run["dry_run_result"]["external_write"] is False

    with pytest.raises(HTTPException) as maker_checker:
        await external_actions.approve(creator, action_id, {"idempotency_key": "approve-self"})
    assert maker_checker.value.status_code == 403

    approved = await external_actions.approve(
        checker,
        action_id,
        {"idempotency_key": "approve-key"},
    )
    assert approved["action"]["status"] == "approved"
    assert approved["action"]["approved_by"] == checker["id"]

    executed = await external_actions.execute(
        checker,
        action_id,
        {"idempotency_key": "execute-key"},
    )
    assert executed["action"]["status"] == "succeeded"
    assert executed["execution_result"]["external_write"] is False
    assert executed["execution_result"]["ok"] is True

    replay_execute = await external_actions.execute(
        checker,
        action_id,
        {"idempotency_key": "execute-key"},
    )
    assert replay_execute["action"]["id"] == action_id
    assert len([event for event in db.events if event["event_type"] == "execute_succeeded"]) == 1


@pytest.mark.asyncio
async def test_execute_revalidates_permission_approval_expiration_flags_and_idempotency(
    external_actions,
    monkeypatch,
):
    db = FakeActionDB()
    _patch_pool(external_actions, monkeypatch, db)
    creator = _user(20, role="workspace_admin")
    checker = _user(21, role="workspace_admin")

    proposed = await external_actions.propose(
        creator,
        {
            "source_type": "control_room",
            "source_id": "item-2",
            "action_type": "sandbox_notify",
            "payload": {},
            "idempotency_key": "proposal-2",
        },
    )
    action_id = proposed["action"]["id"]
    with pytest.raises(HTTPException) as reuse:
        await external_actions.dry_run(creator, action_id, {"idempotency_key": "proposal-2"})
    assert reuse.value.status_code == 409

    await external_actions.dry_run(creator, action_id, {"idempotency_key": "dry-2"})
    with pytest.raises(HTTPException) as not_approved:
        await external_actions.execute(checker, action_id, {"idempotency_key": "execute-too-soon"})
    assert not_approved.value.status_code == 409

    await external_actions.approve(checker, action_id, {"idempotency_key": "approve-2"})
    analyst = _user(22, role="analyst")
    with pytest.raises(HTTPException) as permission:
        await external_actions.execute(analyst, action_id, {"idempotency_key": "execute-analyst"})
    assert permission.value.status_code == 403

    monkeypatch.setenv("EXTERNAL_ACTION_SANDBOX_ENABLED", "false")
    with pytest.raises(HTTPException) as sandbox_disabled:
        await external_actions.execute(checker, action_id, {"idempotency_key": "execute-disabled"})
    assert sandbox_disabled.value.status_code == 409
    monkeypatch.setenv("EXTERNAL_ACTION_SANDBOX_ENABLED", "true")

    db.actions[action_id]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(HTTPException) as expired:
        await external_actions.execute(checker, action_id, {"idempotency_key": "execute-expired"})
    assert expired.value.status_code == 409
    assert db.actions[action_id]["status"] == "expired"
    assert any(event["event_type"] == "expired" for event in db.events)


@pytest.mark.asyncio
async def test_admin_self_approval_is_explicitly_allowed(external_actions, monkeypatch):
    db = FakeActionDB()
    _patch_pool(external_actions, monkeypatch, db)
    admin = _user(30, role="admin")

    proposed = await external_actions.propose(
        admin,
        {
            "source_type": "control_room",
            "source_id": "item-3",
            "action_type": "sandbox_notify",
            "payload": {},
            "idempotency_key": "proposal-3",
        },
    )
    action_id = proposed["action"]["id"]
    await external_actions.dry_run(admin, action_id, {"idempotency_key": "dry-3"})
    approved = await external_actions.approve(admin, action_id, {"idempotency_key": "approve-3"})

    assert approved["action"]["status"] == "approved"
    assert approved["action"]["approved_by"] == admin["id"]


def test_the_sandbox_adapter_is_off_unless_explicitly_enabled(external_actions, monkeypatch):
    monkeypatch.delenv("EXTERNAL_ACTION_SANDBOX_ENABLED", raising=False)
    assert external_actions.sandbox_enabled() is False
