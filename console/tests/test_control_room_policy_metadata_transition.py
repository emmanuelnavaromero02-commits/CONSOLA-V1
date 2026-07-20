from app.services.control_room.business_observation_codec import (
    ENVELOPE_KEY,
    INVALID_ENVELOPE_FIELD,
    POLICY_FIELDS,
)
from app.services.control_room.business_policy_metadata import (
    REPLACED_POLICY_KEYS,
    business_policy_metadata,
    diagnostic_policy_sql,
)


LEGACY_FIELDS = {
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
    "source_item_id": "technical-source",
    "source_dataset": "old_gold",
    "source_status": "missing",
    "source_system": "technical",
}


def _business_item() -> dict:
    return {
        "id": "same-id",
        "kind": "anomaly",
        "source_dataset": "gold_people",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-16",
        "evidence_refs": ["gold_people:same-id"],
    }


def test_technical_metadata_is_replaced_from_the_canonical_policy_schema():
    metadata = {
        **LEGACY_FIELDS,
        ENVELOPE_KEY: {"version": 99, "claims": []},
        "safe_note": "keep",
    }

    clean = business_policy_metadata(metadata, _business_item())

    assert REPLACED_POLICY_KEYS == tuple(sorted(POLICY_FIELDS | {ENVELOPE_KEY}))
    dropped_keys = LEGACY_FIELDS.keys() - {"kind", "source_dataset"}
    assert not dropped_keys & clean.keys()
    assert clean["kind"] == "anomaly"
    assert clean["source_dataset"] == "gold_people"
    assert clean["safe_note"] == "keep"
    assert clean[ENVELOPE_KEY]["version"] == 1
    assert INVALID_ENVELOPE_FIELD not in {
        key for claim in clean[ENVELOPE_KEY]["claims"] for key in claim
    }


def test_nested_technical_metadata_is_replaced_but_safe_details_survive():
    metadata = {
        "details": {**LEGACY_FIELDS, "safe_nested_note": "keep"},
        "safe_note": "keep",
    }

    clean = business_policy_metadata(metadata, _business_item())

    assert clean["details"] == {"safe_nested_note": "keep"}
    assert clean["safe_note"] == "keep"
    assert not POLICY_FIELDS.intersection(clean["details"])


def test_diagnostic_sql_covers_canonical_top_level_and_nested_surfaces():
    sql = diagnostic_policy_sql("items.metadata")

    for field in (
        "data_readiness",
        "data_status",
        "evaluation_status",
        "item_kind",
        "kind",
        "readiness_status",
        "source_status",
    ):
        assert f"items.metadata->>'{field}'" in sql
        assert f"items.metadata->'details'->>'{field}'" in sql
