import json
import uuid

import asyncpg
import pytest

from app.services.control_room.business_item_persistence import (
    ensure_item_row,
    persist_item_rows,
)
from app.services.control_room.business_observation_codec import (
    ENVELOPE_KEY,
    INVALID_ENVELOPE_FIELD,
    POLICY_FIELDS,
)
from app.services.control_room.business_policy_metadata import (
    BUSINESS_ARTIFACT_FIELDS,
    REPLACED_POLICY_KEYS,
)
from app.services.control_room.business_projection import (
    filter_business_items,
    normalize_persisted_business_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_state_rows import state_rows
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    decision_eligibility_provenance,
)
from tests.test_operational_rls_console_refinement import postgres_with_real_init_schema


LEGACY_RESIDUAL_FIELDS = frozenset(
    {
        INVALID_ENVELOPE_FIELD,
        "count",
        "data_status",
        "dataset",
        "derived_from",
        "evaluation_status",
        "evidence_id",
        "evidence_pack_id",
        "gold_table",
        "numerator",
        "numerator_count",
        "parent_item_id",
        "readiness_status",
        "source_item_id",
        "source_status",
        "source_system",
    }
)


def _business_item(item_id: str, tenant_id: str, workspace_id: str) -> dict:
    return {
        "id": item_id,
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
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
        **runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_row={"item_id": item_id},
            locator_field="item_id",
            observed_at="2026-07-16",
        ),
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
        legitimate = _business_item(legitimate_id, tenant_id, workspace_id)
        provenance = decision_eligibility_provenance(
            legitimate,
            decision_id=77,
            workspace_id=workspace_id,
        )
        await conn.execute(
            "INSERT INTO tenants(id, name, slug) VALUES ($1, 'Tenant', $2)",
            tenant_id,
            f"tenant-{uuid.uuid4().hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO workspaces(id, tenant_id, name) VALUES ($1, $2, $3)",
            workspace_id,
            tenant_id,
            f"WS {uuid.uuid4().hex[:8]}",
        )
        await conn.execute(
            """
            INSERT INTO users(id, email, password_hash, is_active)
            VALUES (7, 'owner@example.com', 'x', true), (9, 'admin@example.com', 'x', true)
            ON CONFLICT (id) DO NOTHING
            """
        )
        await conn.execute(
            """
            INSERT INTO decisions(id, title, workspace_id)
            VALUES (42, 'Technical legacy', $1), (77, 'Legitimate workflow', $1)
            ON CONFLICT (id) DO NOTHING
            """,
            workspace_id,
        )
        await conn.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, owner_user_id, item_id, cartridge_id, domain, source_dataset,
                item_kind, title, severity, status, decision_id, metadata,
                selected_option_id, execution_status, first_seen_at, last_seen_at
            ) VALUES
            ($1, $2, 7, $3, 'sap_hcm', 'People', 'old_gold', 'anomaly', 'Old', 'critical',
             'decision_created', 42, $5::jsonb,
             'repair', 'executed', NOW(), NOW()),
            ($1, $2, 7, $4, 'sap_hcm', 'People', 'gold_people', 'anomaly', 'Legit', 'medium',
             'decision_created', 77, $6::jsonb, 'review', 'executed', NOW(), NOW());
            """,
            tenant_id,
            workspace_id,
            technical_id,
            legitimate_id,
            json.dumps(
                {
                    ENVELOPE_KEY: {"version": 99, "claims": []},
                    "observation": {
                        "item_kind": "source_state",
                        "data_status": "missing",
                        "safe_observation": "keep",
                    },
                    "intelligence": {
                        "kind": "source_state",
                        "source_status": "blocked",
                        "safe_intelligence": "keep",
                        "signal": {
                            "item_kind": "source_state",
                            "readiness_status": "insufficient_data",
                            "safe_signal": "keep",
                        },
                    },
                    "details": {
                        INVALID_ENVELOPE_FIELD: True,
                        "count": 0,
                        "data_status": "missing",
                        "dataset": "old_gold",
                        "derived_from": "technical-parent",
                        "evaluation_status": "blocked",
                        "evidence_id": "technical-evidence",
                        "evidence_pack_id": "technical-pack",
                        "gold_table": "old_gold",
                        "item_kind": "source_state",
                        "kind": "source_state",
                        "numerator": 0,
                        "numerator_count": 0,
                        "parent_item_id": "technical-parent",
                        "readiness_status": "insufficient_data",
                        "source_dataset": "old_gold",
                        "source_item_id": "technical-source",
                        "source_status": "missing",
                        "source_system": "technical",
                        "safe_detail": "keep",
                    },
                }
            ),
            json.dumps({DECISION_PROVENANCE_KEY: provenance}),
        )
        rows = state_rows(
            [_business_item(technical_id, tenant_id, workspace_id), legitimate],
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id=9,
            impact_builder=_impact,
            metadata_builder=_metadata,
            diagnostic_builder=_diagnostic,
            owner_by_item={technical_id: 7, legitimate_id: 7},
        )
        await persist_item_rows(conn, rows, workspace_wide=True)
        technical_row = next(row for row in rows if row["item_id"] == technical_id)
        legitimate_row = next(row for row in rows if row["item_id"] == legitimate_id)
        await ensure_item_row(
            conn,
            {**technical_row, "status": "open"},
            terminal_statuses=("approved", "dismissed", "resolved"),
            workspace_wide=True,
        )
        await ensure_item_row(
            conn,
            {
                **legitimate_row,
                "status": "in_review",
                "selected_option_id": "replacement",
                "execution_status": "not_started",
            },
            terminal_statuses=("approved", "dismissed", "resolved"),
            workspace_wide=True,
        )

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
        assert transitioned["status"] == "open"
        assert transitioned["decision_id"] is None
        assert transitioned["selected_option_id"] is None
        assert transitioned["execution_status"] == "not_started"
        assert transitioned_metadata.get("item_kind") != "source_state"
        assert transitioned_metadata.get(WORKFLOW_QUARANTINE_KEY)
        assert not LEGACY_RESIDUAL_FIELDS.intersection(transitioned_metadata)
        assert transitioned_metadata["details"] == {"safe_detail": "keep"}
        assert transitioned_metadata["kind"] == "anomaly"
        assert transitioned_metadata["source_dataset"] == "gold_people"
        assert transitioned_metadata["observation"] == {"safe_observation": "keep"}
        assert "intelligence" not in transitioned_metadata
        assert transitioned_metadata[ENVELOPE_KEY]["version"] == 1
        assert INVALID_ENVELOPE_FIELD not in {
            key
            for claim in transitioned_metadata[ENVELOPE_KEY]["claims"]
            for key in claim
        }
        normalized = normalize_persisted_business_item(transitioned)
        projected = filter_business_items([normalized])
        assert [item["id"] for item in projected] == [technical_id]

        kept = by_id[legitimate_id]
        kept_metadata = _jsonb(kept["metadata"])
        assert kept["status"] == "in_review"
        assert kept["decision_id"] == 77
        assert kept["selected_option_id"] == "review"
        assert kept["execution_status"] == "executed"
        assert kept_metadata[DECISION_PROVENANCE_KEY]["eligible_at_link"] is True
    finally:
        await conn.close()


def test_replaced_policy_keys_follow_the_canonical_policy_schema():
    assert REPLACED_POLICY_KEYS == tuple(
        sorted(POLICY_FIELDS | {ENVELOPE_KEY} | BUSINESS_ARTIFACT_FIELDS)
    )
