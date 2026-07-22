from __future__ import annotations

from app.services.control_room.business_item_persistence import (
    PERSIST_ITEMS_SQL,
    REPLACED_POLICY_KEYS,
)
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    business_observation_fingerprint,
    decision_eligibility_provenance,
    persistence_metadata,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_observation_codec import INVALID_ENVELOPE_FIELD


def _source_row(item: dict) -> dict:
    fields = (
        "id",
        "kind",
        "metric_name",
        "metric_type",
        "observed_value",
        "observation_date",
        "tenant_id",
        "workspace_id",
    )
    return {field: item[field] for field in fields}


def _business_item() -> dict:
    item = {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "metric_name": "affected_people",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-16",
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset=item["source_dataset"],
            source_system=item["source_system"],
            cartridge=item["cartridge"],
            tenant_id=item["tenant_id"],
            workspace_id=item["workspace_id"],
            source_row=_source_row(item),
            locator_field="id",
            observed_at=item["observation_date"],
        ),
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


def test_persist_sql_unlinks_workflow_for_explicit_quarantine():
    sql = " ".join(PERSIST_ITEMS_SQL.split())
    marker = "EXCLUDED.metadata ? 'workflow_quarantine'"
    assert marker in sql
    assert "WHEN (EXCLUDED.metadata ? 'workflow_quarantine'" in sql
    assert "THEN NULL WHEN NOT" in sql
    assert "selected_option_id = CASE" in sql
    assert "THEN 'not_started'" in sql
    assert "THEN 'open'" in sql


def test_eligible_decision_provenance_is_machine_checkable():
    item = _business_item()
    provenance = decision_eligibility_provenance(item, decision_id=42)
    assert provenance["eligible_at_link"] is True
    assert workflow_has_eligible_provenance(
        {DECISION_PROVENANCE_KEY: provenance},
        item,
        decision_id=42,
    )


def test_workflow_provenance_must_match_current_item_identity_and_fingerprint():
    item = _business_item()
    provenance = decision_eligibility_provenance(item, decision_id=42)
    metadata = {DECISION_PROVENANCE_KEY: provenance}

    assert workflow_has_eligible_provenance(metadata, item, decision_id=42)
    assert not workflow_has_eligible_provenance(metadata, item, decision_id=99)
    assert not workflow_has_eligible_provenance(
        metadata, {**item, "id": "other"}, decision_id=42
    )
    assert not workflow_has_eligible_provenance(
        metadata, {**item, "kind": "signal"}, decision_id=42
    )
    assert not workflow_has_eligible_provenance(
        metadata, {**item, "observed_value": 2}, decision_id=42
    )


def test_persistence_metadata_uses_current_fingerprint_and_drops_incoming_links():
    item = _business_item()
    item["metadata"] = {
        DECISION_PROVENANCE_KEY: {"item_id": "foreign"},
        "decision_provenance": {"decision_id": 99},
        "safe": "keep",
    }

    metadata = persistence_metadata(item)

    assert metadata[CURRENT_ELIGIBILITY_FINGERPRINT_KEY] == (
        business_observation_fingerprint(item)
    )
    assert metadata["safe"] == "keep"
    assert DECISION_PROVENANCE_KEY not in metadata
    assert "decision_provenance" not in metadata


def test_persist_sql_replaces_reconciled_workflow_metadata_atomically():
    assert f"- '{DECISION_PROVENANCE_KEY}'" in PERSIST_ITEMS_SQL
    assert "|| EXCLUDED.metadata" in PERSIST_ITEMS_SQL
    assert CURRENT_ELIGIBILITY_FINGERPRINT_KEY in persistence_metadata(_business_item())
