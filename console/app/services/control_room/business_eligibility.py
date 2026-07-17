from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.control_room.business_lineage import (
    has_root_lineage,
    is_derived,
    is_source_state,
    parent_references,
)
from app.services.control_room.business_observation import (
    EVIDENCE_FIELDS,
    SUCCESSFUL_EVALUATION_STATES,
    assess_observation,
    semantic_states,
)


TECHNICAL_STATES = frozenset(
    {
        "blocked",
        "empty",
        "error",
        "insufficient_data",
        "invalid_schema",
        "missing",
        "no_permission",
        "permission_denied",
        "schema_only",
        "stub",
        "unavailable",
    }
)
BUSINESS_OBSERVATION_FIELDS = (
    "observed_at",
    "observation_date",
    "detected_at",
    "as_of",
    "affected_count",
    "count",
    "metric_type",
    "metric_kind",
    "aggregation_type",
    "value_type",
    "metric_value",
    "observed_value",
    "actual_value",
    "value",
    "denominator",
    "denominator_count",
    "population",
    "population_count",
    "sample_count",
    "source_row_count",
    "total_count",
    "value_observed",
    "is_observed",
    "observation_valid",
)
BUSINESS_MATERIALIZATION_FIELDS = (
    "generated_at",
    "materialized_at",
    "polled_at",
    "polling_at",
    "checked_at",
)
BUSINESS_EVIDENCE_FIELDS = EVIDENCE_FIELDS


class EligibilityReason(StrEnum):
    ELIGIBLE = "eligible"
    SOURCE_STATE = "source_state"
    TECHNICAL_STATE = "technical_state"
    INVALID_OBSERVATION = "invalid_observation"
    PARTIAL_WITHOUT_OBSERVATION = "partial_without_observation"
    STALE_WITHOUT_OBSERVATION = "stale_without_observation"
    ZERO_WITHOUT_POPULATION = "zero_without_population"
    INELIGIBLE_PARENT = "ineligible_parent"
    MISSING_LINEAGE = "missing_lineage"
    INVALID_LINEAGE = "invalid_lineage"


@dataclass(frozen=True)
class BusinessEligibility:
    eligible: bool
    reason: EligibilityReason


class BusinessEligibilityError(ValueError):
    code = "item_not_business_eligible"

    def __init__(self, result: BusinessEligibility) -> None:
        self.result = result
        super().__init__(result.reason.value)


def classify_business_item(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> BusinessEligibility:
    if eligible_parent_ids is None:
        projected_parent_ids = getattr(item, "_eligible_parent_ids", None)
        if projected_parent_ids is not None:
            eligible_parent_ids = projected_parent_ids

    if is_source_state(item):
        return BusinessEligibility(False, EligibilityReason.SOURCE_STATE)

    states = semantic_states(item)
    if states & TECHNICAL_STATES:
        return BusinessEligibility(False, EligibilityReason.TECHNICAL_STATE)

    refs = parent_references(item)
    if refs.malformed:
        return BusinessEligibility(False, EligibilityReason.INVALID_LINEAGE)
    if refs.ids and (
        eligible_parent_ids is None or not refs.ids.issubset(eligible_parent_ids)
    ):
        return BusinessEligibility(False, EligibilityReason.INELIGIBLE_PARENT)
    if is_derived(item) and not refs.ids and not has_root_lineage(item):
        return BusinessEligibility(False, EligibilityReason.MISSING_LINEAGE)

    observation = assess_observation(item)
    if "partial" in states and not observation.observed_with_evidence:
        return BusinessEligibility(False, EligibilityReason.PARTIAL_WITHOUT_OBSERVATION)
    if "stale" in states and not (
        observation.observed_with_evidence
        and (bool(refs.ids) or has_root_lineage(item))
    ):
        return BusinessEligibility(False, EligibilityReason.STALE_WITHOUT_OBSERVATION)
    if observation.is_zero and not observation.zero_valid:
        return BusinessEligibility(False, EligibilityReason.ZERO_WITHOUT_POPULATION)
    if observation.invalid_explicit_observation:
        return BusinessEligibility(False, EligibilityReason.INVALID_OBSERVATION)

    return BusinessEligibility(True, EligibilityReason.ELIGIBLE)


def require_business_eligible(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> None:
    result = classify_business_item(item, eligible_parent_ids=eligible_parent_ids)
    if not result.eligible:
        raise BusinessEligibilityError(result)


__all__ = (
    "BUSINESS_EVIDENCE_FIELDS",
    "BUSINESS_MATERIALIZATION_FIELDS",
    "BUSINESS_OBSERVATION_FIELDS",
    "SUCCESSFUL_EVALUATION_STATES",
    "TECHNICAL_STATES",
    "BusinessEligibility",
    "BusinessEligibilityError",
    "EligibilityReason",
    "classify_business_item",
    "require_business_eligible",
)
