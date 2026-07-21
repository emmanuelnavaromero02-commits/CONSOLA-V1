from __future__ import annotations

import pytest

from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
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
            metric_type="scalar",
            observed_value=1,
            source_system="sap",
            evidence_refs=["gold_metrics:row:metric-hardening-1"],
            metadata={"source_dataset": "gold_metrics", "source_system": "sap"},
        )
    )

    assert result.eligible is True


def test_logical_dataset_matches_prefixed_gold_table():
    result = classify_business_item(
        _item(
            metric_type="scalar",
            observed_value=1,
            source_dataset="metrics",
            evidence_refs=["gold_metrics:row:metric-hardening-1"],
            metadata={"details": {"gold_table": "gold_metrics"}},
        )
    )

    assert result.eligible is True


def test_strict_legacy_flat_envelope_is_read_but_rewritten_as_canonical_v1():
    item = _item(
        business_observation={"metric_type": "scalar", "observed_value": 2},
        evidence_refs=["gold_metrics:row:metric-hardening-1"],
    )

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
