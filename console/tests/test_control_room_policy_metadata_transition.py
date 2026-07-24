from app.services.control_room.business_observation_codec import (
    ENVELOPE_KEY,
    INVALID_ENVELOPE_FIELD,
    METADATA_SEMANTIC_SURFACE_PATHS,
    POLICY_FIELDS,
)
from app.services.control_room.business_policy_metadata import (
    BUSINESS_ARTIFACT_FIELDS,
    REPLACED_POLICY_KEYS,
    business_policy_metadata,
    diagnostic_policy_sql,
    policy_metadata_without_fields_sql,
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

    assert REPLACED_POLICY_KEYS == tuple(
        sorted(POLICY_FIELDS | BUSINESS_ARTIFACT_FIELDS | {ENVELOPE_KEY})
    )
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


def test_nested_surface_is_removed_when_only_technical_metadata_remains():
    clean = business_policy_metadata(
        {"details": LEGACY_FIELDS},
        _business_item(),
    )

    assert "details" not in clean


def test_business_intelligence_artifacts_never_survive_policy_transition():
    clean = business_policy_metadata(
        {
            "intelligence": {
                "risk_score": 99,
                "options": [{"id": "legacy-action"}],
                "priority": "critical",
            }
        },
        _business_item(),
    )

    assert "intelligence" not in clean


def test_empty_incoming_details_do_not_replace_existing_safe_metadata():
    item = {**_business_item(), "details": LEGACY_FIELDS}

    clean = business_policy_metadata({"details": {"safe": "keep"}}, item)

    assert clean["details"] == {"safe": "keep"}


def test_nested_observation_is_cleaned_and_intelligence_is_discarded():
    metadata = {
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
    }

    clean = business_policy_metadata(metadata, _business_item())

    assert clean["observation"] == {"safe_observation": "keep"}
    assert "intelligence" not in clean
    assert not POLICY_FIELDS.intersection(clean["observation"])
    assert clean["kind"] == "anomaly"
    assert clean.get("item_kind") != "source_state"


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
        for path in METADATA_SEMANTIC_SURFACE_PATHS:
            surface = (
                "items.metadata"
                if not path
                else f"(items.metadata #> '{{{','.join(path)}}}')"
            )
            assert f"{surface}->>'{field}'" in sql


def test_cleanup_sql_covers_every_nested_metadata_semantic_surface():
    sql = policy_metadata_without_fields_sql("items.metadata", "$2")

    for path in METADATA_SEMANTIC_SURFACE_PATHS:
        if path:
            assert f"items.metadata #> '{{{','.join(path)}}}'" in sql
    assert "$2::text[]" in sql
