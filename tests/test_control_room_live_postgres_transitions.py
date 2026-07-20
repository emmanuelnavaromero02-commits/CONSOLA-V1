from __future__ import annotations

import uuid
import json

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import postgres_with_real_init_schema
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_state_rows import state_rows
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    decision_eligibility_provenance,
)
from app.services.control_room.business_projection import filter_business_items


def _business_item(item_id: str) -> dict:
    return {
        "id": item_id,
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "domain": "People",
        "source_dataset": "gold_people",
        "title": "Valid anomaly",
        "severity": "medium",
        "entity_label": "Employee",
        "anomaly_type": "headcount_gap",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-16",
        "evidence_refs": [f"gold_people:{item_id}"],
    }


def _impact(_item, **_kwargs):
    return {"estimate": 1, "currency": "USD", "confidence": 0.9}


def _metadata(item, _impact_payload):
    return {"kind": item["kind"], "source_dataset": item["source_dataset"]}


def _diagnostic(item):
    return {
        "item_kind": item.get("kind"),
        "kind": item.get("kind"),
        "data_status": item.get("data_status", "missing"),
    }


def _jsonb(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_postgres_transition_cleans_technical_semantics_and_preserves_owner(
    postgres_with_real_init_schema: str,
):
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id = str(uuid.uuid4())
        workspace_id = str(uuid.uuid4())
        technical_id = "same-id"
        legitimate_id = "legit-id"
        legitimate = _business_item(legitimate_id)
        provenance = decision_eligibility_provenance(legitimate, decision_id=77)
        await conn.execute(
            "INSERT INTO tenants(id, name, slug) VALUES ($1, 'Tenant', $2)",
            tenant_id,
            f"tenant-{uuid.uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO workspaces(id, tenant_id, name, slug) VALUES ($1, $2, 'WS', $3)",
            workspace_id,
            tenant_id,
            f"workspace-{uuid.uuid4().hex[:8]}",
        )
        await conn.execute(
            """
            INSERT INTO users(id, email, hashed_password, is_active)
            VALUES (7, 'owner@example.com', 'x', true), (9, 'admin@example.com', 'x', true)
            ON CONFLICT (id) DO NOTHING
            """
        )
        await conn.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, owner_user_id, item_id, cartridge_id, source_dataset,
                item_kind, title, severity, status, decision_id, metadata,
                selected_option_id, execution_status, first_seen_at, last_seen_at
            ) VALUES
            ($1, $2, 7, $3, 'sap_hcm', 'old_gold', 'source_state', 'Old', 'critical',
             'decision_created', 42, '{"item_kind":"source_state","data_status":"missing"}',
             'repair', 'executed', NOW(), NOW()),
            ($1, $2, 7, $4, 'sap_hcm', 'gold_people', 'anomaly', 'Legit', 'medium',
             'decision_created', 77, $5::jsonb, 'review', 'executed', NOW(), NOW());
            """,
            tenant_id,
            workspace_id,
            technical_id,
            legitimate_id,
            json.dumps({DECISION_PROVENANCE_KEY: provenance}),
        )
        rows = state_rows(
            [_business_item(technical_id), legitimate],
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id=9,
            impact_builder=_impact,
            metadata_builder=_metadata,
            diagnostic_builder=_diagnostic,
            owner_by_item={technical_id: 7, legitimate_id: 7},
        )
        await persist_item_rows(conn, rows, workspace_wide=True)

        records = await conn.fetch(
            """
            SELECT item_id, owner_user_id, item_kind, status, decision_id,
                   selected_option_id, execution_status, metadata
              FROM control_room_items
             WHERE workspace_id = $1
             ORDER BY item_id
            """,
            workspace_id,
        )
        by_id = {row["item_id"]: dict(row) for row in records}
        transitioned = by_id[technical_id]
        transitioned_metadata = _jsonb(transitioned["metadata"])
        assert transitioned["owner_user_id"] == 7
        assert transitioned["item_kind"] == "anomaly"
        assert transitioned["decision_id"] is None
        assert transitioned["selected_option_id"] is None
        assert transitioned["execution_status"] == "not_started"
        assert transitioned_metadata.get("item_kind") != "source_state"
        assert transitioned_metadata.get(WORKFLOW_QUARANTINE_KEY)
        assert filter_business_items([{"id": technical_id, **transitioned_metadata}])

        kept = by_id[legitimate_id]
        kept_metadata = _jsonb(kept["metadata"])
        assert kept["decision_id"] == 77
        assert kept["selected_option_id"] == "review"
        assert kept["execution_status"] == "executed"
        assert kept_metadata[DECISION_PROVENANCE_KEY]["eligible_at_link"] is True
    finally:
        await conn.close()
