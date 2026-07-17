from __future__ import annotations

import pytest

from app.services import control_room_service
from app.services.control_room.business_eligibility import (
    BusinessEligibilityError,
    EligibilityReason,
    classify_business_item,
    require_business_eligible,
)
from app.services.control_room.business_projection import (
    diagnostic_items,
    filter_business_items,
)


def _business_item(**overrides):
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "source_dataset": "gold_workforce",
        "status": "open",
        "detected_at": "2026-07-16T10:00:00Z",
    }
    return {**item, **overrides}


@pytest.mark.parametrize(
    "status",
    ["ok", "missing", "blocked", "partial", "stub", "error"],
)
def test_source_state_is_never_business_eligible(status):
    item = _business_item(
        kind="source_state",
        status=status,
        count=15,
        evidence_pack={"items": [{"source": "gold"}]},
        observed_value=7,
    )

    result = classify_business_item(item)

    assert result.eligible is False
    assert result.reason is EligibilityReason.SOURCE_STATE


@pytest.mark.parametrize(
    "state",
    [
        "missing",
        "blocked",
        "stub",
        "error",
        "no_permission",
        "empty",
        "schema_only",
        "unavailable",
        "invalid_schema",
        "insufficient_data",
    ],
)
def test_structured_technical_states_are_ineligible(state):
    result = classify_business_item(
        _business_item(data_status=state, description="texto visible irrelevante")
    )

    assert result.eligible is False
    assert result.reason is EligibilityReason.TECHNICAL_STATE


def test_partial_requires_observation_evidence_date_and_useful_fact():
    incomplete = _business_item(
        data_status="partial",
        evidence_pack={"items": [{"source": "gold"}]},
        detected_at="",
        details={"affected_count": 2},
    )
    complete = {
        **incomplete,
        "detected_at": "2026-07-16T10:00:00Z",
    }

    assert (
        classify_business_item(incomplete).reason
        is EligibilityReason.PARTIAL_WITHOUT_OBSERVATION
    )
    assert classify_business_item(complete).eligible is True


def test_partial_rejects_descriptive_signal_without_measured_fact():
    item = _business_item(
        data_status="partial",
        evidence_pack={"items": [{"source": "gold"}]},
        intelligence={"signal": {"signal_type": "descriptive_only"}},
    )

    assert (
        classify_business_item(item).reason
        is EligibilityReason.PARTIAL_WITHOUT_OBSERVATION
    )


def test_real_zero_requires_success_population_and_observation_date():
    valid = _business_item(
        data_status="gold_ready",
        observed_value=0,
        population_count=25,
    )
    no_population = {**valid, "population_count": 0}

    assert classify_business_item(valid).eligible is True
    assert (
        classify_business_item(no_population).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


def test_metric_value_zero_uses_the_same_zero_policy():
    item = _business_item(
        data_status="ready",
        metric_value=0,
        population_count=0,
    )

    assert (
        classify_business_item(item).reason is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


@pytest.mark.parametrize(
    "measurement",
    [
        {"intelligence": {"signal": {"actual_value": 0}}},
        {"intelligence": {"signal": {"affected_count": 0}}},
        {"source_row_count": 0},
    ],
)
def test_nested_and_count_zeros_cannot_bypass_population_policy(measurement):
    item = _business_item(data_status="ready", population_count=0, **measurement)

    assert (
        classify_business_item(item).reason is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


def test_nested_zero_is_valid_with_success_population_and_date():
    item = _business_item(
        data_status="ready",
        population_count=20,
        intelligence={"signal": {"actual_value": 0}},
    )

    assert classify_business_item(item).eligible is True


def test_stale_valid_observation_remains_business_eligible():
    item = _business_item(
        data_status="stale",
        evidence_pack={"items": [{"source": "gold"}]},
        observed_value=4,
        population_count=10,
    )

    assert classify_business_item(item).eligible is True


def test_workflow_status_and_visible_text_do_not_define_readiness():
    item = _business_item(
        status="open",
        title="Error: missing source",
        description="blocked unavailable",
    )

    assert classify_business_item(item).eligible is True


def test_data_readiness_is_a_structured_state():
    item = _business_item(data_readiness="schema_only")

    assert classify_business_item(item).reason is EligibilityReason.TECHNICAL_STATE


def test_derived_item_requires_eligible_parent():
    item = _business_item(parent_item_id="parent-1")

    assert classify_business_item(item).reason is EligibilityReason.INELIGIBLE_PARENT
    assert (
        classify_business_item(item, eligible_parent_ids={"parent-1"}).eligible is True
    )


def test_parent_in_persisted_metadata_uses_same_lineage_guard():
    item = _business_item(metadata={"parent_item_id": "parent-1"})

    assert classify_business_item(item).reason is EligibilityReason.INELIGIBLE_PARENT
    assert (
        classify_business_item(item, eligible_parent_ids={"parent-1"}).eligible is True
    )


def test_derived_item_without_parent_or_lineage_is_rejected():
    item = {
        "id": "derived-1",
        "kind": "intelligence_signal",
        "status": "open",
    }

    assert classify_business_item(item).reason is EligibilityReason.MISSING_LINEAGE


def test_projection_resolves_parent_before_derived_child():
    parent = _business_item(id="parent-1")
    child = _business_item(
        id="child-1",
        kind="intelligence_signal",
        parent_item_id="parent-1",
    )

    assert filter_business_items([child, parent]) == [child, parent]


def test_persisted_item_kind_source_state_is_rejected():
    item = _business_item(kind=None, item_kind="source_state", data_status="ok")

    assert classify_business_item(item).reason is EligibilityReason.SOURCE_STATE


def test_invalid_zero_remains_ineligible_after_metadata_round_trip():
    item = _business_item(
        data_status="ready",
        observed_value=0,
        population_count=0,
    )
    metadata = control_room_service._metadata_for_item(item, {})
    reloaded = control_room_service._persisted_intelligence_payload(
        {
            "item_id": item["id"],
            "item_kind": item["kind"],
            "source_dataset": item["source_dataset"],
            "metadata": metadata,
        }
    )

    assert reloaded["observed_value"] == 0
    assert reloaded["population_count"] == 0
    assert (
        classify_business_item(reloaded).reason
        is EligibilityReason.ZERO_WITHOUT_POPULATION
    )


def test_projection_removes_historical_business_fields_without_mutating_item():
    contaminated = _business_item(
        kind="source_state",
        omega={"options": [{"score": 92}]},
        priority={"score": 99},
        priority_score=99,
        decision_id=42,
        action_templates=[{"template_id": "create_followup_task"}],
    )
    valid = _business_item(id="business-2")

    projected = filter_business_items([contaminated, valid])
    diagnostic = diagnostic_items([contaminated])[0]

    assert projected == [valid]
    assert "omega" not in diagnostic
    assert "priority" not in diagnostic
    assert "decision_id" not in diagnostic
    assert contaminated["decision_id"] == 42
    assert contaminated["omega"]["options"][0]["score"] == 92


def test_require_business_eligible_raises_stable_domain_error():
    with pytest.raises(BusinessEligibilityError) as exc:
        require_business_eligible(_business_item(kind="source_state"))

    assert exc.value.code == "item_not_business_eligible"
    assert exc.value.result.reason is EligibilityReason.SOURCE_STATE
