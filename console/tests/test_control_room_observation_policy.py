from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import control_room_service
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_observation import assess_observation


def _item(**overrides):
    item = {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-16",
        "evidence_refs": ["gold_metrics:row:metric-1"],
    }
    return {**item, **overrides}


def _round_trip(item: dict) -> dict:
    metadata = control_room_service._metadata_for_item(item, {})
    return control_room_service._persisted_intelligence_payload(
        {
            "item_id": item["id"],
            "item_kind": item["kind"],
            "source_dataset": item.get("source_dataset"),
            "metadata": metadata,
        }
    )


def test_observed_count_zero_with_known_empty_population_is_eligible():
    result = classify_business_item(
        _item(metric_type="count", count=0, population_count=0)
    )

    assert result.eligible is True


def test_observed_rate_zero_requires_positive_denominator():
    valid = _item(
        metric_type="rate",
        observed_value=0,
        denominator=20,
        population_count=20,
    )
    invalid = {**valid, "denominator": 0}

    assert classify_business_item(valid).eligible is True
    assert (
        classify_business_item(invalid).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


@pytest.mark.parametrize(
    "missing",
    ["data_status", "observation_date", "population_count"],
)
def test_count_zero_fails_closed_without_required_observation_context(missing):
    item = _item(metric_type="count", count=0, population_count=0)
    item.pop(missing)

    assert (
        classify_business_item(item).reason is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


@pytest.mark.parametrize(
    "measurement",
    [
        {"count": None},
        {"count": 0, "value_observed": False},
        {"count": 0, "observation_valid": False},
    ],
)
def test_null_or_default_zero_is_not_an_observed_zero(measurement):
    item = _item(metric_type="count", population_count=0, **measurement)

    assessment = assess_observation(item)

    assert assessment.has_measured_fact is False
    assert assessment.is_zero is False
    assert classify_business_item(item).reason is EligibilityReason.INVALID_OBSERVATION


def test_explicit_null_measurement_without_status_fails_closed():
    result = classify_business_item(
        _item(data_status=None, metric_type="count", count=None)
    )

    assert result.reason is EligibilityReason.INVALID_OBSERVATION


@pytest.mark.parametrize("invalid_value", ["N/A", "not-a-number", {}, float("nan")])
def test_non_measurement_values_fail_closed(invalid_value):
    result = classify_business_item(
        _item(
            data_status="partial",
            observed_value=invalid_value,
            evidence_refs=["evidence:1"],
        )
    )

    assert result.reason is EligibilityReason.PARTIAL_WITHOUT_OBSERVATION


def test_contradictory_measurements_fail_closed():
    result = classify_business_item(
        _item(observed_value=1, metadata={"observed_value": 2})
    )

    assert result.reason is EligibilityReason.INVALID_OBSERVATION


def test_nested_null_cannot_be_hidden_by_top_level_observed_value():
    result = classify_business_item(
        _item(
            data_status="partial",
            observed_value=2,
            evidence_refs=["evidence:1"],
            metadata={"details": {"observed_value": None}},
        )
    )

    assert result.reason is EligibilityReason.PARTIAL_WITHOUT_OBSERVATION


def test_source_state_count_zero_remains_diagnostic():
    result = classify_business_item(
        _item(
            kind="source_state",
            metric_type="count",
            count=0,
            population_count=0,
        )
    )

    assert result.reason is EligibilityReason.SOURCE_STATE


def test_metric_type_is_not_inferred_from_visible_text():
    result = classify_business_item(
        _item(
            title="Conteo de personas",
            description="count metric",
            count=0,
            population_count=0,
        )
    )

    assert result.reason is EligibilityReason.INVALID_OBSERVATION


def test_stale_requires_prior_observation_evidence_fact_and_lineage():
    base = _item(
        data_status="stale",
        metric_type="scalar",
        observed_value=4,
        evidence_refs=["evidence:1"],
    )

    assert classify_business_item(base).eligible is True
    assert (
        classify_business_item({**base, "evidence_refs": []}).reason
        is EligibilityReason.STALE_WITHOUT_OBSERVATION
    )
    assert (
        classify_business_item({**base, "observed_value": None}).reason
        is EligibilityReason.STALE_WITHOUT_OBSERVATION
    )
    without_date = {
        key: value for key, value in base.items() if key != "observation_date"
    }
    assert (
        classify_business_item(without_date).reason
        is EligibilityReason.STALE_WITHOUT_OBSERVATION
    )
    without_lineage = {
        key: value for key, value in base.items() if key != "source_dataset"
    }
    assert (
        classify_business_item(without_lineage).reason
        is EligibilityReason.STALE_WITHOUT_OBSERVATION
    )


@pytest.mark.parametrize(
    "item,expected",
    [
        (
            _item(
                data_status="partial",
                observation_date=None,
                generated_at="2026-07-16T12:00:00Z",
                count=2,
                evidence_refs=["evidence:1"],
            ),
            EligibilityReason.PARTIAL_WITHOUT_OBSERVATION,
        ),
        (
            _item(metric_type="rate", observed_value=0, denominator=0),
            EligibilityReason.ZERO_WITHOUT_POPULATION,
        ),
        (
            _item(
                data_status="stale",
                observation_date=None,
                last_seen_at="2026-07-16T12:00:00Z",
                observed_value=2,
                evidence_refs=["evidence:1"],
            ),
            EligibilityReason.STALE_WITHOUT_OBSERVATION,
        ),
        (
            _item(
                metric_type="count",
                count=0,
                population_count=0,
                evidence_refs=["evidence:1"],
            ),
            EligibilityReason.ELIGIBLE,
        ),
        (
            _item(data_status=None, metric_type="count", count=None),
            EligibilityReason.INVALID_OBSERVATION,
        ),
    ],
)
def test_business_classification_is_stable_after_persistence(item, expected):
    before = classify_business_item(item)
    after = classify_business_item(_round_trip(item))

    assert before.reason is expected
    assert after.reason is expected


def test_materialization_and_polling_dates_do_not_prove_observation():
    item = _item(
        data_status="partial",
        observation_date=None,
        generated_at="2026-07-16T12:00:00Z",
        materialized_at="2026-07-16T12:01:00Z",
        polled_at="2026-07-16T12:02:00Z",
        count=1,
        evidence_refs=["evidence:1"],
    )

    assert (
        classify_business_item(item).reason
        is EligibilityReason.PARTIAL_WITHOUT_OBSERVATION
    )


def test_future_date_object_is_not_a_valid_stale_observation():
    item = _item(
        data_status="stale",
        observation_date=date.today() + timedelta(days=1),
        observed_value=2,
        evidence_refs=["evidence:1"],
    )

    assert (
        classify_business_item(item).reason
        is EligibilityReason.STALE_WITHOUT_OBSERVATION
    )


def test_future_date_string_is_not_a_valid_stale_observation():
    item = _item(
        data_status="stale",
        observation_date=str(date.today() + timedelta(days=1)),
        observed_value=2,
        evidence_refs=["evidence:1"],
    )

    assert (
        classify_business_item(item).reason
        is EligibilityReason.STALE_WITHOUT_OBSERVATION
    )


def test_nested_metadata_details_participates_in_fail_closed_policy():
    item = _item(
        metadata={
            "details": {
                "source_status": "missing",
                "parent_item_id": "diagnostic-parent",
            }
        }
    )

    assert classify_business_item(item).reason is EligibilityReason.TECHNICAL_STATE
