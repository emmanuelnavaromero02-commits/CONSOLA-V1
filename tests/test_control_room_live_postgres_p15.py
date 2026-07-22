from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from app.services import control_room_service as service
from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_guarded_action_reservation,
)
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_execution_precondition import dry_run_metadata
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_repository import (
    approve_control_room_decision,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.test_control_room_live_postgres_workflows import (
    AsyncNoop,
    _item,
    _rows,
    _scope,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)

# fmt: off
INTERNAL_CASES = (
    ("create_followup_task", service._execute_internal_followup_task_tx, "action_executed"),
    ("create_investigation_note", service._execute_internal_investigation_note, "investigation_note_created"),
    ("mark_decision_for_monitoring", service._execute_internal_decision_monitoring, "decision_monitoring_marked"),
)
# fmt: on


async def _linked_item(dsn: str, item_id: str):
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item = _item(item_id, tenant_id, workspace_id)
        await persist_item_rows(
            conn,
            _rows([item], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        decision = await create_and_link_decision(
            conn,
            user={"id": 7, "email": "workflow-owner@example.com"},
            item={**item, "owner_user_id": 7},
            workspace_id=workspace_id,
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
        )
        decision_id = int(decision["id"])
        linked_item = {
            **item,
            "owner_user_id": 7,
            "decision_id": decision_id,
            "status": "decision_created",
        }
        await approve_control_room_decision(
            conn,
            workspace_id=workspace_id,
            item_id=item["id"],
            decision_id=decision_id,
            owner_user_id=7,
            item=linked_item,
            lessons=[],
        )
        item["owner_user_id"] = 7
        item["decision_id"] = decision_id
        item["status"] = "approved"
        item["execution_status"] = "dry_run_validated"
        return tenant_id, workspace_id, item
    finally:
        await conn.close()


def _user(tenant_id: str, workspace_id: str) -> dict:
    return {
        "id": 7,
        "email": "workflow-owner@example.com",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "_effective_permissions": ["control_room.write", "control_room.execute"],
    }


async def _seed_matching_dry_run(
    dsn: str,
    *,
    tenant_id: str,
    workspace_id: str,
    item: dict,
    template_id: str,
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        await conn.execute(
            """
            INSERT INTO action_runs (
                tenant_id, workspace_id, item_id, decision_id, action_type,
                adapter_name, mode, status, idempotency_key, actor_id,
                actor_email, dry_run_result, metadata, completed_at
            )
            VALUES ($1, $2, $3, $4, $5,
                    'test', 'dry_run', 'dry_run_completed', $6, 7,
                    'workflow-owner@example.com', $7::jsonb, $8::jsonb, NOW())
            """,
            tenant_id,
            workspace_id,
            item["id"],
            int(item["decision_id"]),
            template_id,
            f"dry-run:{item['id']}:{template_id}",
            json.dumps({"ok": True, "validated": True}),
            json.dumps(dry_run_metadata(item, template_id=template_id)),
        )
        await conn.execute(
            """UPDATE control_room_items
                  SET execution_status='dry_run_validated'
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item["id"],
        )
    finally:
        await conn.close()


def _template(template_id: str, cartridge: str = "platform") -> dict:
    return {
        "template_id": template_id,
        "template_type": template_id,
        "cartridge_id": cartridge,
        "label": template_id,
        "action_kind": "test",
        "risk_level": "low",
        "requires_approval": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("template_id,runner,event_type", INTERNAL_CASES)
async def test_live_internal_effect_is_once_under_concurrency(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    template_id: str,
    runner,
    event_type: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, f"p15-{template_id}"
    )
    user = _user(tenant_id, workspace_id)
    template = _template(template_id)
    payload = {"mode": "execute_live", "action_payload": {}}
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
    )
    barrier = asyncio.Barrier(2)

    async def execute():
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                await barrier.wait()
                return await runner(
                    conn,
                    user=user,
                    item=item,
                    template=template,
                    payload=payload,
                    idempotency_key=None,
                    ip=None,
                    user_agent=None,
                )
        finally:
            await conn.close()

    results = await asyncio.gather(execute(), execute())
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        assert sorted(result["idempotent"] for result in results) == [False, True]
        run_count = await check.fetchval(
            "SELECT COUNT(*) FROM action_runs "
            "WHERE workspace_id=$1 AND item_id=$2 AND mode='execute'",
            workspace_id,
            item["id"],
        )
        event_count = await check.fetchval(
            "SELECT COUNT(*) FROM control_room_item_events "
            "WHERE workspace_id=$1 AND item_id=$2 AND event_type=$3",
            workspace_id,
            item["id"],
            event_type,
        )
        assert (run_count, event_count) == (1, 1)
    finally:
        await check.close()


@pytest.mark.asyncio
async def test_live_external_remote_call_is_once_and_uses_reserved_key(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, "p15-external"
    )
    user = _user(tenant_id, workspace_id)
    template = _template("p15_external", cartridge="replicon")
    payload = {"mode": "execute_live", "action_payload": {}}
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id="p15_external",
    )

    class Adapter(service.BaseAdapter):
        supports_idempotency = True
        calls: list[str] = []

        async def execute(self, action_data, credentials, dry_run=True):
            self.calls.append(action_data["idempotency_key"])
            await asyncio.sleep(0.05)
            return service.ExecutionResult(True, "executed", "ok", {"id": "remote-1"})

    monkeypatch.setattr(
        service.WriteBackAdapterFactory,
        "get_adapter",
        classmethod(lambda _cls, _template_type: Adapter()),
    )
    barrier = asyncio.Barrier(2)

    async def execute():
        await barrier.wait()
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                reservation = await acquire_guarded_action_reservation(
                    conn,
                    user=user,
                    item=item,
                    template_id="p15_external",
                    adapter_name="Adapter",
                    operation="execute",
                    input_payload=payload,
                )
        finally:
            await conn.close()
        if reservation.state is not ReservationState.ACQUIRED:
            return reservation
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                return await service._execute_external_writeback(
                    conn,
                    user=user,
                    item=item,
                    template=template,
                    payload=payload,
                    reservation=reservation,
                    ip=None,
                    user_agent=None,
                )
        finally:
            await conn.close()

    await asyncio.gather(execute(), execute())
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        row = await check.fetchrow(
            "SELECT idempotency_key, status FROM action_runs "
            "WHERE workspace_id=$1 AND item_id=$2 AND mode='execute'",
            workspace_id,
            item["id"],
        )
        assert row["status"] == "completed"
        assert Adapter.calls == [row["idempotency_key"]]
    finally:
        await check.close()
