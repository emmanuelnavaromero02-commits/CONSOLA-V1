from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


TECHNICAL_STATES = frozenset(
    {
        "blocked",
        "empty",
        "error",
        "insufficient_data",
        "invalid_schema",
        "missing",
        "no_permission",
        "schema_only",
        "stub",
        "unavailable",
    }
)
SUCCESSFUL_EVALUATION_STATES = frozenset(
    {"complete", "gold_ready", "materialized", "ok", "ready", "success"}
)
BUSINESS_OBSERVATION_FIELDS = (
    "observed_at",
    "generated_at",
    "materialized_at",
    "affected_count",
    "metric_value",
    "observed_value",
    "source_row_count",
    "value",
    "population",
    "population_count",
    "sample_count",
    "total_count",
)
BUSINESS_EVIDENCE_FIELDS = (
    "analysis_evidence",
    "evidence",
    "evidence_pack",
    "evidence_refs",
)


class EligibilityReason(StrEnum):
    ELIGIBLE = "eligible"
    SOURCE_STATE = "source_state"
    TECHNICAL_STATE = "technical_state"
    PARTIAL_WITHOUT_OBSERVATION = "partial_without_observation"
    ZERO_WITHOUT_POPULATION = "zero_without_population"
    INELIGIBLE_PARENT = "ineligible_parent"
    MISSING_LINEAGE = "missing_lineage"


@dataclass(frozen=True)
class BusinessEligibility:
    eligible: bool
    reason: EligibilityReason


class BusinessEligibilityError(ValueError):
    code = "item_not_business_eligible"

    def __init__(self, result: BusinessEligibility) -> None:
        self.result = result
        super().__init__(result.reason.value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _semantic_maps(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    details = _mapping(item.get("details"))
    metadata = _mapping(item.get("metadata"))
    intelligence = _mapping(item.get("intelligence"))
    return item, details, metadata, intelligence


def _semantic_states(item: Mapping[str, Any]) -> set[str]:
    states: set[str] = set()
    for values in _semantic_maps(item):
        for key in (
            "data_status",
            "data_readiness",
            "evaluation_status",
            "readiness_status",
            "source_status",
        ):
            value = str(values.get(key) or "").strip().lower()
            if value:
                states.add(value)
    return states


def _first_present(item: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for values in _semantic_maps(item):
        for key in keys:
            if key in values and values.get(key) not in (None, ""):
                return values.get(key)
    return None


def _has_observed_at(item: Mapping[str, Any]) -> bool:
    return (
        _first_present(
            item,
            (
                "observed_at",
                "detected_at",
                "freshness_at",
                "materialized_at",
                "generated_at",
            ),
        )
        is not None
    )


def _has_evidence(item: Mapping[str, Any]) -> bool:
    for values in _semantic_maps(item):
        for key in ("analysis_evidence", "evidence", "evidence_pack", "evidence_refs"):
            evidence = values.get(key)
            if isinstance(evidence, Mapping) and evidence:
                return True
            if isinstance(evidence, (list, tuple)) and evidence:
                return True
    intelligence = _mapping(item.get("intelligence"))
    evidence_pack = _mapping(intelligence.get("evidence_pack"))
    return bool(evidence_pack)


def _has_useful_fact(item: Mapping[str, Any]) -> bool:
    value = _first_present(
        item,
        (
            "affected_count",
            "metric_value",
            "observed_value",
            "source_row_count",
            "value",
        ),
    )
    if value is not None:
        return True
    intelligence = _mapping(item.get("intelligence"))
    signal = _mapping(intelligence.get("signal"))
    return any(
        key in signal and signal.get(key) not in (None, "")
        for key in (
            "actual_value",
            "affected_count",
            "deviation_pct",
            "deviation_value",
            "metric_value",
            "observed_value",
            "value",
        )
    )


def _explicit_observed_values(item: Mapping[str, Any]) -> tuple[Any, ...]:
    values: list[Any] = []
    observation = _mapping(item.get("observation"))
    if "value" in observation:
        values.append(observation.get("value"))
    for semantic in _semantic_maps(item):
        for key in (
            "actual_value",
            "affected_count",
            "metric_value",
            "observed_value",
            "source_row_count",
            "value",
        ):
            if key in semantic:
                values.append(semantic.get(key))
    signal = _mapping(_mapping(item.get("intelligence")).get("signal"))
    for key in (
        "actual_value",
        "affected_count",
        "metric_value",
        "observed_value",
        "source_row_count",
        "value",
    ):
        if key in signal:
            values.append(signal.get(key))
    return tuple(values)


def _is_zero(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _known_population(item: Mapping[str, Any]) -> bool:
    value = _first_present(
        item,
        (
            "population",
            "population_count",
            "source_row_count",
            "sample_count",
            "total_count",
        ),
    )
    try:
        return value is not None and float(value) > 0
    except (TypeError, ValueError):
        return False


def _has_lineage(item: Mapping[str, Any]) -> bool:
    return _first_present(
        item,
        ("source_dataset", "dataset", "gold_table", "control_origin", "source_system"),
    ) is not None or _has_evidence(item)


def _is_derived(item: Mapping[str, Any]) -> bool:
    kind = str(item.get("kind") or item.get("item_kind") or "").strip().lower()
    return bool(
        kind in {"agent_alert", "derived", "intelligence_signal"}
        or item.get("derived_from")
        or item.get("parent_item_id")
        or item.get("source_item_id")
        or _mapping(item.get("lineage"))
    )


def _parent_id(item: Mapping[str, Any]) -> str:
    lineage = _mapping(_first_present(item, ("lineage",)))
    return str(
        _first_present(item, ("parent_item_id", "source_item_id"))
        or lineage.get("parent_item_id")
        or ""
    ).strip()


def classify_business_item(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> BusinessEligibility:
    if eligible_parent_ids is None:
        projected_parent_ids = getattr(item, "_eligible_parent_ids", None)
        if projected_parent_ids is not None:
            eligible_parent_ids = projected_parent_ids
    kind = str(item.get("kind") or item.get("item_kind") or "").strip().lower()
    if kind == "source_state":
        return BusinessEligibility(False, EligibilityReason.SOURCE_STATE)

    states = _semantic_states(item)
    if states & TECHNICAL_STATES:
        return BusinessEligibility(False, EligibilityReason.TECHNICAL_STATE)

    parent_id = _parent_id(item)
    if parent_id and (
        eligible_parent_ids is None or parent_id not in eligible_parent_ids
    ):
        return BusinessEligibility(False, EligibilityReason.INELIGIBLE_PARENT)

    if _is_derived(item) and not _has_lineage(item):
        return BusinessEligibility(False, EligibilityReason.MISSING_LINEAGE)

    if "partial" in states and not (
        _has_observed_at(item) and _has_evidence(item) and _has_useful_fact(item)
    ):
        return BusinessEligibility(False, EligibilityReason.PARTIAL_WITHOUT_OBSERVATION)

    observed_values = _explicit_observed_values(item)
    if any(_is_zero(value) for value in observed_values):
        if not (
            states & SUCCESSFUL_EVALUATION_STATES
            and _known_population(item)
            and _has_observed_at(item)
        ):
            return BusinessEligibility(False, EligibilityReason.ZERO_WITHOUT_POPULATION)

    return BusinessEligibility(True, EligibilityReason.ELIGIBLE)


def require_business_eligible(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> None:
    result = classify_business_item(item, eligible_parent_ids=eligible_parent_ids)
    if not result.eligible:
        raise BusinessEligibilityError(result)
