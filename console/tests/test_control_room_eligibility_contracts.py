from __future__ import annotations

import pytest

from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_observation import has_evidence
from app.services.control_room.business_observation_codec import observation_envelope
from app.services.control_room.business_policy_metadata import (
    BUSINESS_ARTIFACT_FIELDS,
    REPLACED_POLICY_KEYS,
    business_policy_metadata,
)
from app.services.control_room.business_projection import (
    filter_business_items,
    strip_business_fields,
)


def _metric(**overrides):
    item = {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-20",
        "metric_type": "count",
        "count": 1,
        "evidence_refs": ["gold_metrics:row:1"],
    }
    return {**item, **overrides}


def test_bare_item_without_observation_fails_closed():
    item = {
        "id": "bare-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "status": "open",
    }

    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_metric_requires_date_typed_fact_and_structured_evidence():
    assert classify_business_item(_metric()).eligible is True

    for missing in ("observation_date", "count", "evidence_refs"):
        item = _metric()
        item.pop(missing)
        assert (
            classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION
        )


def test_qualitative_anomaly_requires_runtime_source_identity_date_and_evidence():
    item = {
        "id": "anomaly-1",
        "kind": "anomaly",
        "source_system": "sap_successfactors",
        "source_dataset": "gold_workforce",
        "entity_id": "employee-7",
        "detected_at": "2026-07-20T10:00:00Z",
        "evidence_refs": ["gold_workforce:anomaly:1"],
    }
    assert classify_business_item(item).eligible is True

    for missing in ("source_system", "entity_id", "detected_at", "evidence_refs"):
        incomplete = dict(item)
        incomplete.pop(missing)
        assert (
            classify_business_item(incomplete).reason
            is EligibilityReason.INVALID_OBSERVATION
        )


def test_count_zero_rejects_empty_unknown_and_negative_population():
    known_empty = _metric(count=0, population_count=0)
    assert (
        classify_business_item(known_empty).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )

    unknown = dict(known_empty)
    unknown.pop("population_count")
    assert (
        classify_business_item(unknown).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )
    assert (
        classify_business_item(_metric(count=0, population_count=-1)).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


def test_empty_count_stays_ineligible_after_canonical_observation_round_trip():
    original = _metric(count=0, population_count=0)
    restored = {
        "id": original["id"],
        "kind": original["kind"],
        "source_dataset": original["source_dataset"],
        "metadata": {"business_observation": observation_envelope(original)},
    }

    assert (
        classify_business_item(restored).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


@pytest.mark.parametrize("metric_type", ["rate", "percentage", "average", "division"])
def test_zero_ratio_metrics_require_positive_denominator(metric_type):
    valid = _metric(
        metric_type=metric_type,
        count=None,
        observed_value=0,
        denominator=4,
    )
    invalid = {**valid, "denominator": 0}

    assert classify_business_item(valid).eligible is True
    assert (
        classify_business_item(invalid).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


@pytest.mark.parametrize(
    "evidence",
    [
        {"evidence_id": "analysis complete"},
        {"evidence_pack_id": "workspace-7", "workspace_id": "workspace-7"},
        {"evidence_id": "tenant-7", "tenant_id": "tenant-7"},
        {
            "evidence_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
        {"evidence_pack": {"id": "owner-7"}, "owner_user_id": "owner-7"},
        {"evidence": {"summary": "verified by analyst"}},
    ],
)
def test_narrative_or_administrative_references_are_not_evidence(evidence):
    assert has_evidence(_metric(evidence_refs=[], **evidence)) is False


def test_evidence_id_with_source_identity_is_accepted():
    assert has_evidence(_metric(evidence_refs=[], evidence_id="evidence-17")) is True


def test_non_administrative_uuid_evidence_id_is_accepted():
    assert (
        has_evidence(
            _metric(
                evidence_refs=[],
                evidence_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            )
        )
        is True
    )


def test_evidence_id_without_source_identity_is_rejected():
    assert has_evidence({"evidence_id": "evidence-17"}) is False


def test_conflicting_dataset_and_lineage_root_fail_closed():
    item = _metric(lineage={"root_source": "gold_other"})

    assert classify_business_item(item).reason is EligibilityReason.INVALID_LINEAGE


def test_matching_dataset_and_lineage_root_are_not_contradictory():
    assert classify_business_item(
        _metric(lineage={"root_source": "gold_metrics"})
    ).eligible


def _contaminated_metadata():
    return {
        "neutral": {"label": "kept"},
        "intelligence": {
            "options": [{"id": "fabricated"}],
            "decision_intelligence": {"score": 99},
            "priority": {"score": 99},
            "monte_carlo": {"p95": 10},
            "bayesian_calibration": {"posterior": 0.9},
            "suggested_actions": [{"id": "fake"}],
            "action_templates": [{"id": "fake"}],
            "omega": {"score": 99},
            "workflow": {
                "decision_id": 42,
                "selected_option_id": "fake",
                "execution_status": "executed",
            },
        },
    }


def test_nested_technical_workflow_artifacts_are_removed_recursively():
    sanitized = strip_business_fields({"metadata": _contaminated_metadata()})

    assert sanitized == {"metadata": {"neutral": {"label": "kept"}}}


def test_business_projection_does_not_revive_nested_historical_artifacts():
    item = _metric(metadata=_contaminated_metadata())

    projected = filter_business_items([item])[0]

    assert projected["metadata"] == {"neutral": {"label": "kept"}}
    assert item["metadata"]["intelligence"]["workflow"]["decision_id"] == 42


def test_server_artifacts_survive_after_initial_business_sanitization():
    projected = filter_business_items([_metric(metadata=_contaminated_metadata())])[0]
    projected["math_provenance"] = {"monte_carlo": {"status": "not_applicable"}}

    refreshed = filter_business_items([projected])[0]

    assert refreshed["math_provenance"]["monte_carlo"] == {"status": "not_applicable"}


def test_business_metadata_transition_preserves_only_neutral_nested_metadata():
    cleaned = business_policy_metadata(_contaminated_metadata(), _metric())

    assert cleaned["neutral"] == {"label": "kept"}
    assert "intelligence" not in cleaned
    assert "business_observation" in cleaned


def test_jsonb_transition_replaces_nested_business_artifacts():
    assert BUSINESS_ARTIFACT_FIELDS <= frozenset(REPLACED_POLICY_KEYS)
