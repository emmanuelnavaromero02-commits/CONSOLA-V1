from __future__ import annotations

import json

import asyncpg
import pytest

from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_persistence import (
    persist_item_rows,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    business_observation_fingerprint,
)
from tests.test_control_room_live_postgres_workflows import _rows, _scope
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


def _item(
    item_id: str,
    tenant_id: str,
    workspace_id: str,
    *,
    value: int,
    observed_at: str,
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


class AsyncNoop:
    async def __call__(self, *_args, **_kwargs):
        return None


def _metadata(value) -> dict:
    return json.loads(value) if isinstance(value, str) else dict(value)


@pytest.mark.asyncio
async def test_live_new_observation_starts_b_workflow_and_preserves_a(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item_a = _item(
            "workflow-cycle",
            tenant_id,
            workspace_id,
            value=1,
            observed_at="2026-07-20",
        )
        item_b = _item(
            "workflow-cycle",
            tenant_id,
            workspace_id,
            value=2,
            observed_at="2026-07-21",
        )
        user = {"id": 7, "email": "workflow-owner@example.com"}
        await persist_item_rows(
            conn,
            _rows([item_a], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        decision_a = await create_and_link_decision(
            conn,
            user=user,
            item={**item_a, "owner_user_id": 7},
            workspace_id=workspace_id,
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
        )

        await persist_item_rows(
            conn,
            _rows([item_b], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        unlinked = dict(
            await conn.fetchrow(
                "SELECT * FROM control_room_items WHERE workspace_id=$1 AND item_id=$2",
                workspace_id,
                item_b["id"],
            )
        )
        unlinked_metadata = _metadata(unlinked["metadata"])
        generations = unlinked_metadata[WORKFLOW_QUARANTINE_KEY]["generations"]
        assert unlinked["status"] == "open"
        assert unlinked["decision_id"] is None
        assert unlinked["selected_option_id"] is None
        assert unlinked["execution_status"] == "not_started"
        assert DECISION_PROVENANCE_KEY not in unlinked_metadata
        assert generations[0]["decision_id"] == decision_a["id"]

        decision_b = await create_and_link_decision(
            conn,
            user=user,
            item={**item_b, "owner_user_id": 7},
            workspace_id=workspace_id,
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
        )
        linked = dict(
            await conn.fetchrow(
                "SELECT * FROM control_room_items WHERE workspace_id=$1 AND item_id=$2",
                workspace_id,
                item_b["id"],
            )
        )
        linked_metadata = _metadata(linked["metadata"])
        assert decision_b["id"] != decision_a["id"]
        assert linked["decision_id"] == decision_b["id"]
        assert (
            linked_metadata[DECISION_PROVENANCE_KEY]["decision_id"] == decision_b["id"]
        )
        assert linked_metadata[DECISION_PROVENANCE_KEY]["fingerprint"] == (
            business_observation_fingerprint(item_b)
        )
        assert linked_metadata[WORKFLOW_QUARANTINE_KEY]["generations"] == generations
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM decisions WHERE id=$1)", decision_a["id"]
        )
    finally:
        await conn.close()
