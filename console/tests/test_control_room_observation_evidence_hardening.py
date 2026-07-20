from __future__ import annotations

import pytest

from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_observation import has_evidence
from app.services.control_room.business_observation_codec import (
    ENVELOPE_VERSION,
    INVALID_ENVELOPE_FIELD,
    observation_envelope,
    persisted_claims,
)


def _item(**overrides):
    item = {
        "id": "metric-hardening-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-16",
    }
    return {**item, **overrides}


@pytest.mark.parametrize(
    ("metric_kind", "measurement", "supporting_only"),
    [
        ("count", {"count": 3}, {"population_count": 10}),
        ("rate", {"observed_value": 0.25}, {"numerator": 1, "denominator": 4}),
        (
            "percentage",
            {"observed_value": 25},
            {"numerator": 1, "denominator": 4},
        ),
        ("average", {"observed_value": 8}, {"source_row_count": 4}),
        ("division", {"observed_value": 2}, {"numerator": 8, "denominator": 4}),
        ("amount", {"observed_value": 125.5}, {"population_count": 4}),
        ("scalar", {"observed_value": 7}, {"source_row_count": 4}),
        ("unknown", {"observed_value": 9}, {"source_row_count": 4}),
    ],
)
def test_every_declared_metric_kind_requires_its_value_slot(
    metric_kind,
    measurement,
    supporting_only,
):
    valid = classify_business_item(_item(metric_type=metric_kind, **measurement))
    missing = classify_business_item(_item(metric_type=metric_kind, **supporting_only))

    assert valid.eligible is True
    assert missing.reason is EligibilityReason.INVALID_OBSERVATION


def test_count_explicitly_accepts_affected_count_as_its_value():
    result = classify_business_item(
        _item(metric_type="count", affected_count=3, population_count=10)
    )

    assert result.eligible is True


@pytest.mark.parametrize(
    "supporting_only",
    [
        {"population_count": 10},
        {"numerator": 2},
        {"denominator": 10},
        {"source_row_count": 10},
    ],
)
def test_supporting_slots_never_prove_a_business_fact(supporting_only):
    result = classify_business_item(_item(**supporting_only))

    assert result.reason is EligibilityReason.INVALID_OBSERVATION


@pytest.mark.parametrize(
    "evidence",
    [
        {"evidence_pack": {"metadata": {"tenant_id": "tenant-a"}}},
        {"evidence_pack": {"metadata": {"workspace_id": "workspace-a"}}},
        {"evidence_pack": {"metadata": {"owner_user_id": "7"}}},
        {"evidence_pack": {"account_id": "account-a"}},
        {"evidence_pack": {"project_id": "project-a"}},
        {"evidence_id": "N/A"},
        {"evidence_refs": ["unknown", "null", "missing"]},
        {"evidence_pack": {"id": "none"}},
        {"evidence_pack": {"items": [{"source_ref": "not available"}]}},
    ],
)
def test_administrative_ids_and_placeholders_are_not_evidence(evidence):
    assert has_evidence(_item(**evidence)) is False


@pytest.mark.parametrize(
    "evidence",
    [
        {"evidence_id": "evidence-17"},
        {"evidence_pack": {"id": "pack-17"}},
        {"evidence_pack": {"artifact_id": "artifact-17"}},
        {"evidence_refs": ["s3://bucket/evidence/17.json"]},
        {"evidence_pack": {"items": [{"source_ref": "gold_metrics:17"}]}},
    ],
)
def test_allowlisted_evidence_ids_and_references_are_substantive(evidence):
    assert has_evidence(_item(**evidence)) is True


@pytest.mark.parametrize(
    "conflict",
    [
        {"metadata": {"source_dataset": "gold_other"}},
        {"metadata": {"dataset": "gold_other"}},
        {"gold_table": "gold_other"},
        {"details": {"gold_table": "gold_other"}},
        {"metadata": {"gold_table": "gold_other"}},
        {"metadata": {"details": {"gold_table": "gold_other"}}},
        {"metadata": {"source_system": "other_system"}, "source_system": "sap"},
        {"lineage": {"source_dataset": "gold_other"}},
    ],
)
def test_contradictory_root_sources_fail_closed(conflict):
    result = classify_business_item(_item(observed_value=1, **conflict))

    assert result.reason is EligibilityReason.INVALID_LINEAGE


def test_matching_root_sources_and_distinct_source_system_remain_valid():
    result = classify_business_item(
        _item(
            observed_value=1,
            source_system="sap",
            metadata={"source_dataset": "gold_metrics", "source_system": "sap"},
        )
    )

    assert result.eligible is True


def test_logical_dataset_matches_prefixed_gold_table():
    result = classify_business_item(
        _item(
            observed_value=1,
            source_dataset="metrics",
            metadata={"details": {"gold_table": "gold_metrics"}},
        )
    )

    assert result.eligible is True


def test_strict_legacy_flat_envelope_is_read_but_rewritten_as_canonical_v1():
    item = _item(business_observation={"metric_type": "scalar", "observed_value": 2})

    assert persisted_claims(item) == ({"metric_type": "scalar", "observed_value": 2},)
    assert classify_business_item(item).eligible is True
    canonical = observation_envelope(item)
    assert canonical["version"] == ENVELOPE_VERSION
    assert {"metric_type": "scalar", "observed_value": 2} in canonical["claims"]


@pytest.mark.parametrize(
    "legacy",
    [
        {"claims": [{"observed_value": 2}]},
        {"observed_value": 2, "unknown_field": "value"},
        {"metadata": {"observed_value": 2}},
    ],
)
def test_legacy_flat_envelope_rejects_wrappers_and_unknown_fields(legacy):
    claims = persisted_claims(_item(business_observation=legacy))

    assert claims == ({INVALID_ENVELOPE_FIELD: True},)
    assert (
        classify_business_item(_item(business_observation=legacy)).reason
        is EligibilityReason.INVALID_OBSERVATION
    )
