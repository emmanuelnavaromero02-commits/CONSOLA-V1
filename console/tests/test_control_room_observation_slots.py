from __future__ import annotations

import pytest

from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_observation import (
    assess_observation,
    has_evidence,
)
from app.services.control_room.business_semantic_slots import (
    SemanticSlot,
    resolve_semantic_slots,
)


SEMANTIC_LOCATIONS = (
    "top_level",
    "details",
    "metadata",
    "metadata_details",
    "observation",
    "intelligence",
    "intelligence_signal",
    "persisted_observation",
)


def _item(**overrides):
    item = {
        "id": "metric-slots-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-16",
        "evidence_refs": ["gold_metrics:row:metric-slots-1"],
    }
    return {**item, **overrides}


def _at_location(location: str, payload: dict) -> dict:
    if location == "top_level":
        return payload
    if location == "details":
        return {"details": payload}
    if location == "metadata":
        return {"metadata": payload}
    if location == "metadata_details":
        return {"metadata": {"details": payload}}
    if location == "observation":
        return {"observation": payload}
    if location == "intelligence":
        return {"intelligence": payload}
    if location == "intelligence_signal":
        return {"intelligence": {"signal": payload}}
    if location == "persisted_observation":
        return {"metadata": {"business_observation": payload}}
    raise AssertionError(f"unsupported semantic location: {location}")


@pytest.mark.parametrize("location", SEMANTIC_LOCATIONS)
def test_same_slot_conflict_fails_closed_at_every_semantic_location(location):
    item = _item(observed_value=1, **_at_location(location, {"actual_value": 2}))

    assessment = assess_observation(item)

    assert assessment.has_measured_fact is False
    assert assessment.invalid_explicit_observation is True
    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_same_slot_numeric_aliases_can_agree_across_locations():
    item = _item(
        metric_type="ratio",
        observed_value="0.10",
        metadata={
            "metric_kind": "rate",
            "details": {"actual_value": 0.1},
            "business_observation": {"metric_value": 0.10},
        },
    )

    assert classify_business_item(item).eligible is True


def test_typed_rate_slots_do_not_cross_compare_or_supply_the_rate():
    item = _item(
        metric_type="rate",
        observed_value=0.10,
        numerator=10,
        denominator=100,
        population_count=250,
        affected_count=10,
        source_row_count=100,
    )

    slots = resolve_semantic_slots(item)

    assert slots.observed_value.value == 0.10
    assert slots.numerator.value == 10
    assert slots.denominator.value == 100
    assert slots.population.value == 250
    assert slots.affected_count.value == 10
    assert slots.source_rows.value == 100
    assert {slot.slot for slot in slots.values} == set(SemanticSlot)
    assert classify_business_item(item).eligible is True


def test_observed_count_and_affected_count_are_distinct_slots():
    item = _item(
        metric_type="count",
        count=3,
        affected_count=2,
        population_count=10,
    )

    slots = resolve_semantic_slots(item)

    assert slots.observed_value.value == 3
    assert slots.affected_count.value == 2
    assert classify_business_item(item).eligible is True


@pytest.mark.parametrize(
    "non_rate_values",
    [
        {"affected_count": 10, "denominator": 100},
        {"source_row_count": 100, "denominator": 100},
        {"numerator": 10, "denominator": 100},
    ],
)
def test_rate_without_observed_value_is_ineligible(non_rate_values):
    result = classify_business_item(_item(metric_type="rate", **non_rate_values))

    assert result.reason is EligibilityReason.INVALID_OBSERVATION


def test_denominator_alias_contradiction_fails_closed():
    item = _item(
        metric_type="rate",
        observed_value=0.1,
        denominator=100,
        metadata={"details": {"denominator_count": 99}},
    )

    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_population_alias_contradiction_fails_closed():
    item = _item(
        metric_type="count",
        count=2,
        population_count=100,
        intelligence={"signal": {"sample_count": 99}},
    )

    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_metric_kind_contradiction_fails_closed():
    item = _item(
        metric_type="rate",
        observed_value=0.1,
        observation={"aggregation_type": "count"},
    )

    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_observation_flag_contradiction_fails_closed():
    item = _item(
        observed_value=2,
        observation={"value_observed": True},
        intelligence={"signal": {"is_observed": False}},
    )

    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_count_zero_accepts_known_empty_population():
    item = _item(metric_type="count", count=0, population_count=0)

    assert classify_business_item(item).eligible is True


@pytest.mark.parametrize(
    ("metric_type", "measurement"),
    [
        ("count", {"count": 0}),
        ("rate", {"observed_value": 0, "denominator": 10}),
        ("percentage", {"observed_value": 0, "denominator": 10}),
        ("average", {"observed_value": 0, "denominator": 10}),
        ("division", {"observed_value": 0, "denominator": 10}),
        ("amount", {"observed_value": 0}),
        ("scalar", {"observed_value": 0}),
    ],
)
def test_real_zero_uses_metric_specific_population_rules(
    metric_type,
    measurement,
):
    valid = _item(metric_type=metric_type, population_count=10, **measurement)
    empty_population = {**valid, "population_count": 0}

    assert classify_business_item(valid).eligible is True
    assert classify_business_item(empty_population).eligible is True


@pytest.mark.parametrize("missing", ["data_status", "observation_date"])
def test_real_zero_requires_successful_evaluation_and_date(missing):
    item = _item(metric_type="count", count=0, population_count=10)
    item.pop(missing)

    assert (
        classify_business_item(item).reason is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


@pytest.mark.parametrize(
    "evidence_pack",
    [
        {},
        {"items": []},
        {"items": {}},
        {"items": [{}, []]},
        {"items": [{"metadata": {}}]},
    ],
)
def test_empty_evidence_pack_structures_are_not_evidence(evidence_pack):
    item = _item(
        data_status="partial",
        observed_value=1,
        evidence_refs=[],
        evidence_pack=evidence_pack,
    )

    assert has_evidence(item) is False
    assert (
        classify_business_item(item).reason
        is EligibilityReason.PARTIAL_WITHOUT_OBSERVATION
    )


@pytest.mark.parametrize(
    "evidence",
    [
        {"evidence_pack_id": "pack-17"},
        {"evidence_pack": {"id": "pack-17", "items": []}},
        {"evidence_refs": ["gold_metrics:row:17"]},
        {"evidence_pack": {"items": [{"source_ref": "gold_metrics"}]}},
    ],
)
def test_stable_ids_and_substantive_references_are_evidence(evidence):
    item = _item(
        data_status="partial",
        observed_value=1,
        **{"evidence_refs": [], **evidence},
    )

    assert has_evidence(item) is True
    assert classify_business_item(item).eligible is True


def test_blank_evidence_ids_and_references_are_not_evidence():
    item = _item(
        evidence_pack_id=" ",
        evidence_pack={"id": "", "items": [" "]},
        evidence_refs=["", "  "],
    )

    assert has_evidence(item) is False
