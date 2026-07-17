from __future__ import annotations

import json

import pytest

from app.services import control_room_service
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)


def _base_item(**overrides):
    item = {
        "id": "roundtrip-1",
        "kind": "intelligence_signal",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "metric_type": "count",
        "count": 2,
        "population_count": 10,
        "observation_date": "2026-07-16",
        "evidence_refs": ["gold_metrics:roundtrip-1"],
    }
    return {**item, **overrides}


def _persisted_round_trip(item: dict, *, diagnostic: bool = False) -> dict:
    metadata = (
        control_room_service._diagnostic_metadata(item)
        if diagnostic
        else control_room_service._metadata_for_item(item, {})
    )
    stored_metadata = json.loads(json.dumps(metadata, default=str))
    return control_room_service._persisted_intelligence_payload(
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "item_id": item["id"],
            "item_kind": item["kind"],
            "source_dataset": item.get("source_dataset"),
            "metadata": stored_metadata,
        }
    )


@pytest.mark.parametrize(
    "overrides,reason",
    [
        (
            {"data_status": "ready", "metadata": {"data_status": "missing"}},
            EligibilityReason.TECHNICAL_STATE,
        ),
        (
            {
                "parent_item_id": "parent-a",
                "metadata": {"parent_item_id": "parent-b"},
            },
            EligibilityReason.INVALID_LINEAGE,
        ),
        (
            {
                "source_item_id": "source-a",
                "metadata": {"source_item_id": "source-b"},
            },
            EligibilityReason.INVALID_LINEAGE,
        ),
        (
            {"metric_type": "rate", "metadata": {"metric_type": "count"}},
            EligibilityReason.INVALID_OBSERVATION,
        ),
        (
            {
                "metric_type": "rate",
                "observed_value": 0.1,
                "denominator": 100,
                "metadata": {"denominator": 20},
            },
            EligibilityReason.INVALID_OBSERVATION,
        ),
    ],
)
def test_semantic_conflicts_survive_two_persistence_round_trips(overrides, reason):
    item = _base_item(**overrides)

    first = _persisted_round_trip(item)
    second = _persisted_round_trip(first)

    assert classify_business_item(item).reason is reason
    assert classify_business_item(first).reason is reason
    assert classify_business_item(second).reason is reason


def test_diagnostic_metadata_preserves_conflict_across_round_trip():
    item = _base_item(
        data_status="ready",
        metadata={"data_status": "missing"},
    )

    persisted = _persisted_round_trip(item, diagnostic=True)

    assert classify_business_item(persisted).reason is EligibilityReason.TECHNICAL_STATE


def test_valid_observation_is_stable_across_two_persistence_round_trips():
    item = _base_item(
        metric_type="rate",
        count=None,
        observed_value=0.1,
        numerator=10,
        denominator=100,
        source_row_count=100,
    )

    first = _persisted_round_trip(item)
    second = _persisted_round_trip(first)

    assert classify_business_item(item).eligible is True
    assert classify_business_item(first).eligible is True
    assert classify_business_item(second).eligible is True


@pytest.mark.parametrize(
    "envelope",
    [
        {"version": 2, "claims": [{"data_status": "missing"}]},
        {"version": 1, "claims": "not-a-list"},
        {"version": 1, "claims": [{"data_status": "ready"}, "invalid"]},
        {"claims": [{"kind": "source_state", "data_status": "missing"}]},
        {"version": 1, "claims": [{"kind": ["source_state"]}]},
        {"version": 1, "claims": [{"data_status": ["missing"]}]},
        "invalid",
    ],
)
def test_invalid_observation_envelope_fails_closed_across_round_trip(envelope):
    item = _base_item(business_observation=envelope)

    persisted = _persisted_round_trip(item)

    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION
    assert (
        classify_business_item(persisted).reason
        is EligibilityReason.INVALID_OBSERVATION
    )
