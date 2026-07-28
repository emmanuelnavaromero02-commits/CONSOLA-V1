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
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_execution_precondition import dry_run_metadata
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_repository import (
    approve_control_room_decision,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_live_action_binding_seed import persist_live_action_binding
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
    ("create_followup_task", "action_executed"),
    ("create_investigation_note", "investigation_note_created"),
    ("mark_decision_for_monitoring", "decision_monitoring_marked"),
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


def _mutation_item(item: dict) -> dict:
    return {key: value for key, value in item.items() if key != "execution_status"}


async def _seed_matching_dry_run(
    dsn: str,
    *,
    tenant_id: str,
    workspace_id: str,
    item: dict,
    template_id: str,
) -> None:
    template = _template(template_id)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        await conn.execute(
            """
            INSERT INTO control_room_action_templates (
                template_id, cartridge_id, label, description, action_kind,
                risk_level, mode_default, requires_approval, config
            )
            VALUES ($1, $2, $3, $4, $5,
                    $6, $7, $8, '{}'::jsonb)
            ON CONFLICT (template_id) DO NOTHING
            """,
            template_id,
            template["cartridge_id"],
            template["label"],
            template["description"],
            template["action_kind"],
            template["risk_level"],
            template["mode_default"],
            template["requires_approval"],
        )
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


def _template(template_id: str) -> dict:
    return dict(ACTION_TEMPLATES[template_id])


@pytest.mark.asyncio
@pytest.mark.parametrize("template_id,event_type", INTERNAL_CASES)
async def test_live_internal_effect_is_once_under_concurrency(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    template_id: str,
    event_type: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, f"p15-{template_id}"
    )
    user = {
        **_user(tenant_id, workspace_id),
        "role": "super_admin",
        "allowed_cartridges": ["*"],
    }
    item, binding_id = await persist_live_action_binding(
        postgres_with_real_init_schema,
        omega_console_live_dsn,
        user=user,
        item_id=item["id"],
        template_id=template_id,
    )
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
    )
    item["execution_status"] = "dry_run_validated"
    barrier = asyncio.Barrier(2)
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=6)

    async def load_same_snapshot(*_args, **_kwargs):
        await barrier.wait()
        return item

    async def pool_factory():
        return pool

    monkeypatch.setattr(service.auth, "pool", pool_factory)
    monkeypatch.setattr(service, "_item_for_mutation", load_same_snapshot)

    async def execute():
        return await service.execute_item(
            item["id"],
            user,
            template_id=template_id,
            binding_id=binding_id,
            confirm_execute=True,
            idempotency_key=None,
        )

    try:
        results = await asyncio.gather(execute(), execute())
    finally:
        await pool.close()
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        assert sorted(result["idempotent"] for result in results) == [False, True]
        assert {result["item"]["execution_status"] for result in results} == {
            "executed"
        }
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
        workflow_provenance = await check.fetchrow(
            """SELECT metadata->'decision_eligibility_provenance'->>'stage' AS stage,
                      metadata->'decision_eligibility_provenance'->>'reason' AS reason
                 FROM control_room_items
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item["id"],
        )
        assert (run_count, event_count) == (1, 1)
        assert dict(workflow_provenance) == {
            "stage": "executed",
            "reason": "explicit_execution",
        }
    finally:
        await check.close()
