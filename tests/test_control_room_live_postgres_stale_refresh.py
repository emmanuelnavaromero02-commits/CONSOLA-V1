from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_observation_order import (
    OBSERVATION_ORDER_KEY,
    business_observation_order,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    business_observation_fingerprint,
)
from tests.test_control_room_live_postgres_workflows import (
    AsyncNoop,
    _rows,
    _scope,
)
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


def _generation(
    item_id: str,
    tenant_id: str,
    workspace_id: str,
    *,
    observed_at: str,
    value: int,
    title: str,
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
        "title": title,
        "description": "Measured headcount gap",
        "recommendation": "Review staffing",
        "severity": "high",
        "entity_label": "Employee",
        "observed_value": value,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": observed_at,
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
            observed_at=observed_at,
            business_observation=item,
        ),
    }


@pytest.mark.asyncio
async def test_live_stale_refresh_cannot_replace_or_quarantine_newer_workflow(
    postgres_with_real_init_schema: str,
) -> None:
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(setup)
    finally:
        await setup.close()

    old = _generation(
        "stale-refresh-item",
        tenant_id,
        workspace_id,
        observed_at="2026-07-20T10:00:00Z",
        value=1,
        title="Old generation",
    )
    current = _generation(
        old["id"],
        tenant_id,
        workspace_id,
        observed_at="2026-07-20T18:00:00Z",
        value=2,
        title="Current generation",
    )
    old_collected = asyncio.Event()
    current_linked = asyncio.Event()
    linked_decision: dict = {}
    current_last_seen = None

    async def stale_refresh() -> None:
        old_collected.set()
        await current_linked.wait()
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            await persist_item_rows(
                conn,
                _rows([old], tenant_id, workspace_id),
                owner_scope_id=7,
            )
        finally:
            await conn.close()

    async def current_refresh_and_link() -> None:
        nonlocal current_last_seen
        await old_collected.wait()
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            await persist_item_rows(
                conn,
                _rows([current], tenant_id, workspace_id),
                owner_scope_id=7,
            )
            async with conn.transaction():
                linked_decision.update(
                    await create_and_link_decision(
                        conn,
                        user={"id": 7, "email": "workflow-owner@example.com"},
                        item={**current, "owner_user_id": 7},
                        workspace_id=workspace_id,
                        ensure_item_row=AsyncNoop(),
                        record_item_event=AsyncNoop(),
                    )
                )
            await conn.execute(
                "UPDATE control_room_items "
                "SET metadata=metadata - $3::text "
                "WHERE workspace_id=$1 AND item_id=$2",
                workspace_id,
                current["id"],
                OBSERVATION_ORDER_KEY,
            )
            current_last_seen = await conn.fetchval(
                "SELECT last_seen_at FROM control_room_items "
                "WHERE workspace_id=$1 AND item_id=$2",
                workspace_id,
                current["id"],
            )
        finally:
            await conn.close()
        current_linked.set()

    await asyncio.gather(stale_refresh(), current_refresh_and_link())

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        stored = await check.fetchrow(
            "SELECT title, status, decision_id, selected_option_id, "
            "execution_status, last_seen_at, metadata "
            "FROM control_room_items WHERE workspace_id=$1 AND item_id=$2",
            workspace_id,
            current["id"],
        )
        metadata = json.loads(stored["metadata"])
        assert stored["title"] == "Current generation"
        assert stored["status"] == "decision_created"
        assert stored["decision_id"] == linked_decision["id"]
        assert stored["selected_option_id"] is None
        assert stored["execution_status"] == "not_started"
        assert stored["last_seen_at"] == current_last_seen
        assert metadata[OBSERVATION_ORDER_KEY] == business_observation_order(current)
        assert metadata[DECISION_PROVENANCE_KEY]["fingerprint"] == (
            business_observation_fingerprint(current)
        )
        assert WORKFLOW_QUARANTINE_KEY not in metadata
    finally:
        await check.close()
