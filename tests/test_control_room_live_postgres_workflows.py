from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest

from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_repository import decision_provenance
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_state_rows import state_rows
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    workflow_has_eligible_provenance,
)
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


def _item(
    item_id: str,
    tenant_id: str,
    workspace_id: str,
    *,
    observed_value: int = 1,
    observation_date: str = "2026-07-20",
) -> dict:
    item = {
        "id": item_id,
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "domain": "People",
        "source_dataset": "gold_people",
        "title": "Measured anomaly",
        "entity_label": "Employee",
        "description": "Measured headcount gap",
        "recommendation": "Review staffing",
        "severity": "high",
        "observed_value": observed_value,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": observation_date,
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_row={**item, "item_id": item_id},
            locator_field="item_id",
            observed_at=observation_date,
            business_observation=item,
        ),
    }


async def _scope(conn) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    await conn.execute(
        "INSERT INTO tenants(id, name, slug) VALUES ($1, $2, $3)",
        tenant_id,
        f"Tenant {suffix}",
        f"tenant-{suffix}",
    )
    await conn.execute(
        "INSERT INTO workspaces(id, tenant_id, name) VALUES ($1, $2, 'Workflow')",
        workspace_id,
        tenant_id,
    )
    await conn.execute(
        """INSERT INTO users(id, email, password_hash, is_active)
           VALUES (7, 'workflow-owner@example.com', 'x', true),
                  (9, 'workflow-admin@example.com', 'x', true)
           ON CONFLICT (id) DO NOTHING"""
    )
    return tenant_id, workspace_id


def _rows(items, tenant_id: str, workspace_id: str, owner=7):
    return state_rows(
        items,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        owner_user_id=owner,
        impact_builder=lambda *_args, **_kwargs: {"estimate": 1},
        metadata_builder=lambda item, _impact: business_policy_metadata({}, item),
        diagnostic_builder=lambda item: {
            "kind": item.get("kind"),
            "data_status": item.get("data_status", "missing"),
        },
    )


@pytest.mark.asyncio
async def test_live_refresh_backfills_only_demonstrable_legacy_workflow(
    postgres_with_real_init_schema: str,
):
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        legitimate = _item("legacy-legitimate", tenant_id, workspace_id)
        technical = _item("legacy-technical", tenant_id, workspace_id)
        ids = {}
        for name, item in (("legitimate", legitimate), ("technical", technical)):
            row = await conn.fetchrow(
                """INSERT INTO decisions(title, workspace_id, kpis)
                   VALUES ($1, $2, $3::jsonb) RETURNING id""",
                name,
                workspace_id,
                json.dumps([decision_provenance("control_room", item_id=item["id"])]),
            )
            ids[name] = int(row["id"])
            await conn.execute(
                """INSERT INTO decision_actions(decision_id, action_text, actor)
                   VALUES ($1, 'Created from Control Room', 'owner')""",
                row["id"],
            )
        await conn.execute(
            """INSERT INTO control_room_items(
                   tenant_id, workspace_id, owner_user_id, item_id, cartridge_id,
                   domain, source_dataset, item_kind, title, severity, status,
                   decision_id, selected_option_id, execution_status, metadata)
               VALUES
                   ($1, $2, 7, $3, 'sap_hcm', 'People', 'gold_people', 'anomaly',
                    'Legitimate', 'high', 'approved', $5, 'review', 'executed', $7::jsonb),
                   ($1, $2, 7, $4, 'sap_hcm', 'People', 'old_debug', 'source_state',
                    'Technical', 'high', 'approved', $6, 'repair', 'executed', $8::jsonb)""",
            tenant_id,
            workspace_id,
            legitimate["id"],
            technical["id"],
            ids["legitimate"],
            ids["technical"],
            json.dumps(business_policy_metadata({}, legitimate)),
            json.dumps({"kind": "source_state", "data_status": "missing"}),
        )
        await conn.execute(
            """INSERT INTO control_room_item_events(
                   tenant_id, workspace_id, item_id, event_type, metadata)
               VALUES ($1, $2, $3, 'decision_created', $4::jsonb)""",
            tenant_id,
            workspace_id,
            legitimate["id"],
            json.dumps({"decision_id": ids["legitimate"]}),
        )

        await persist_item_rows(
            conn,
            _rows([legitimate, technical], tenant_id, workspace_id, owner=9),
            workspace_wide=True,
        )
        records = await conn.fetch(
            """SELECT * FROM control_room_items
               WHERE workspace_id=$1 AND item_id=ANY($2::text[])""",
            workspace_id,
            [legitimate["id"], technical["id"]],
        )
        rows = {row["item_id"]: dict(row) for row in records}
        legit = rows[legitimate["id"]]
        legit_meta = json.loads(legit["metadata"])
        assert legit["owner_user_id"] == 7
        assert legit["decision_id"] == ids["legitimate"]
        assert legit["selected_option_id"] == "review"
        assert legit["execution_status"] == "executed"
        assert workflow_has_eligible_provenance(
            legit_meta,
            {
                **legitimate,
                "workspace_id": workspace_id,
                "selected_option_id": "review",
            },
            decision_id=ids["legitimate"],
        )

        quarantined = rows[technical["id"]]
        quarantine_meta = json.loads(quarantined["metadata"])
        assert quarantined["status"] == "open"
        assert quarantined["decision_id"] is None
        assert quarantined["selected_option_id"] is None
        assert quarantined["execution_status"] == "not_started"
        assert quarantine_meta.get(WORKFLOW_QUARANTINE_KEY)
        assert DECISION_PROVENANCE_KEY not in quarantine_meta
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM decisions WHERE id=$1)",
            ids["technical"],
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_concurrent_decision_creation_is_idempotent(
    postgres_with_real_init_schema: str,
):
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(setup)
        item = _item("concurrent-item", tenant_id, workspace_id)
        await persist_item_rows(
            setup,
            _rows([item], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        before = await setup.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id=$1", workspace_id
        )
    finally:
        await setup.close()

    async def create():
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            async with conn.transaction():
                return await create_and_link_decision(
                    conn,
                    user={"id": 7, "email": "workflow-owner@example.com"},
                    item={**item, "owner_user_id": 7},
                    workspace_id=workspace_id,
                    ensure_item_row=AsyncNoop(),
                    record_item_event=AsyncNoop(),
                )
        finally:
            await conn.close()

    first, second = await asyncio.gather(create(), create())
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        linked = await check.fetchval(
            "SELECT decision_id FROM control_room_items WHERE workspace_id=$1 AND item_id=$2",
            workspace_id,
            item["id"],
        )
        after = await check.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id=$1", workspace_id
        )
        orphaned = await check.fetchval(
            """SELECT COUNT(*) FROM decisions d
               LEFT JOIN control_room_items i ON i.decision_id=d.id
               WHERE d.workspace_id=$1 AND i.decision_id IS NULL""",
            workspace_id,
        )
        assert first["id"] == second["id"] == linked
        assert after - before == 1
        assert orphaned == 0
    finally:
        await check.close()


class AsyncNoop:
    async def __call__(self, *_args, **_kwargs):
        return None
