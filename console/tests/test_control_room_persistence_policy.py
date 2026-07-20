from __future__ import annotations

from app.services.control_room.business_item_persistence import (
    PERSIST_ITEMS_SQL,
    REPLACED_POLICY_KEYS,
)
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    decision_eligibility_provenance,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_observation_codec import INVALID_ENVELOPE_FIELD


def _business_item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "source_dataset": "gold_people",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-16",
        "evidence_refs": ["gold_people:item-1"],
    }


def test_replaced_policy_keys_cover_legacy_source_state_fields():
    for key in (
        "kind",
        "item_kind",
        "source_dataset",
        "dataset",
        "gold_table",
        "source_system",
        "evidence_id",
        "evidence_pack_id",
        "numerator",
        "numerator_count",
        INVALID_ENVELOPE_FIELD,
    ):
        assert key in REPLACED_POLICY_KEYS


def test_business_policy_metadata_drops_legacy_source_state_semantics():
    metadata = {
        "item_kind": "source_state",
        "kind": "source_state",
        "data_status": "missing",
        "source_status": "missing",
        "safe_note": "keep",
    }

    clean = business_policy_metadata(metadata, _business_item())

    assert clean["kind"] == "anomaly"
    assert clean["source_dataset"] == "gold_people"
    assert clean["safe_note"] == "keep"
    assert clean.get("item_kind") != "source_state"
    assert clean.get("data_status") != "missing"


def test_persist_sql_resets_unproven_workflow_on_diagnostic_transition():
    assert "decision_id = CASE WHEN" in PERSIST_ITEMS_SQL
    assert "THEN NULL ELSE control_room_items.decision_id END" in PERSIST_ITEMS_SQL
    assert (
        "workflow_quarantine" in PERSIST_ITEMS_SQL or "$4::jsonb" in PERSIST_ITEMS_SQL
    )


def test_eligible_decision_provenance_is_machine_checkable():
    provenance = decision_eligibility_provenance(_business_item(), decision_id=42)
    assert provenance["eligible_at_link"] is True
    assert workflow_has_eligible_provenance({DECISION_PROVENANCE_KEY: provenance})
