from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any


SUCCESSFUL_EVALUATION_STATES = frozenset(
    {"complete", "gold_ready", "materialized", "ok", "ready", "success"}
)
OBSERVATION_DATE_FIELDS = (
    "observed_at",
    "observation_date",
    "detected_at",
    "as_of",
)
EVIDENCE_FIELDS = (
    "analysis_evidence",
    "evidence",
    "evidence_pack",
    "evidence_refs",
)


class MetricKind(StrEnum):
    COUNT = "count"
    RATE = "rate"
    PERCENTAGE = "percentage"
    AVERAGE = "average"
    DIVISION = "division"
    AMOUNT = "amount"
    SCALAR = "scalar"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ObservationAssessment:
    has_observation_date: bool
    has_evidence: bool
    has_measured_fact: bool
    invalid_explicit_observation: bool
    is_zero: bool
    zero_valid: bool

    @property
    def observed_with_evidence(self) -> bool:
        return (
            self.has_observation_date and self.has_evidence and self.has_measured_fact
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def semantic_maps(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    details = _mapping(item.get("details"))
    metadata = _mapping(item.get("metadata"))
    metadata_details = _mapping(metadata.get("details"))
    intelligence = _mapping(item.get("intelligence"))
    signal = _mapping(intelligence.get("signal"))
    observation = _mapping(item.get("observation"))
    persisted_observation = _mapping(metadata.get("business_observation"))
    return (
        item,
        observation,
        details,
        metadata,
        metadata_details,
        persisted_observation,
        intelligence,
        signal,
    )


def semantic_states(item: Mapping[str, Any]) -> set[str]:
    states: set[str] = set()
    for values in semantic_maps(item):
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


def _first_present(item: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[bool, Any]:
    for values in semantic_maps(item):
        for key in keys:
            if key in values:
                return True, values.get(key)
    return False, None


def _valid_datetime(value: Any) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value <= datetime.now(UTC).date()
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(text)
            except ValueError:
                return False
            return parsed_date <= datetime.now(UTC).date()
    else:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


def has_observation_date(item: Mapping[str, Any]) -> bool:
    values = [
        values.get(key)
        for values in semantic_maps(item)
        for key in OBSERVATION_DATE_FIELDS
        if key in values and values.get(key) != ""
    ]
    return bool(values) and all(_valid_datetime(value) for value in values)


def has_evidence(item: Mapping[str, Any]) -> bool:
    for values in semantic_maps(item):
        for key in EVIDENCE_FIELDS:
            evidence = values.get(key)
            if isinstance(evidence, Mapping) and evidence:
                return True
            if isinstance(evidence, (list, tuple)) and evidence:
                return True
    return False


def _metric_kind(item: Mapping[str, Any]) -> MetricKind:
    _, raw = _first_present(
        item,
        ("metric_type", "metric_kind", "aggregation_type", "value_type"),
    )
    value = str(raw or "").strip().lower()
    aliases = {
        "counter": MetricKind.COUNT,
        "number": MetricKind.COUNT,
        "percent": MetricKind.PERCENTAGE,
        "ratio": MetricKind.RATE,
        "mean": MetricKind.AVERAGE,
        "currency": MetricKind.AMOUNT,
        "numeric": MetricKind.SCALAR,
    }
    if value in aliases:
        return aliases[value]
    try:
        return MetricKind(value)
    except ValueError:
        return MetricKind.UNKNOWN


def _measurement(item: Mapping[str, Any], kind: MetricKind) -> tuple[bool, bool, Any]:
    if kind is MetricKind.COUNT:
        keys = (
            "count",
            "affected_count",
            "observed_value",
            "metric_value",
            "actual_value",
            "value",
            "source_row_count",
        )
    else:
        keys = (
            "observed_value",
            "metric_value",
            "actual_value",
            "value",
            "count",
            "affected_count",
            "source_row_count",
        )
    declared = False
    invalid = False
    values: list[tuple[Any, float]] = []
    for semantic in semantic_maps(item):
        for key in keys:
            if key not in semantic:
                continue
            declared = True
            value = semantic.get(key)
            number = _number(value)
            if number is None:
                invalid = True
            else:
                values.append((value, number))
        for key in ("value_observed", "is_observed", "observation_valid"):
            if key in semantic:
                declared = True
                invalid = invalid or semantic.get(key) is not True
    if invalid or not values or len({number for _value, number in values}) != 1:
        return declared, False, None
    return declared, True, values[0][0]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _known_population(item: Mapping[str, Any]) -> float | None:
    present, value = _first_present(
        item,
        (
            "population",
            "population_count",
            "sample_count",
            "total_count",
            "source_row_count",
        ),
    )
    return _number(value) if present else None


def _denominator(item: Mapping[str, Any]) -> float | None:
    present, value = _first_present(
        item,
        (
            "denominator",
            "denominator_count",
            "population",
            "population_count",
            "sample_count",
            "total_count",
        ),
    )
    return _number(value) if present else None


def _zero_is_valid(item: Mapping[str, Any], kind: MetricKind) -> bool:
    states = semantic_states(item)
    if not states & SUCCESSFUL_EVALUATION_STATES or not has_observation_date(item):
        return False
    if kind is MetricKind.COUNT:
        population = _known_population(item)
        return population is not None and population >= 0
    if kind in {
        MetricKind.RATE,
        MetricKind.PERCENTAGE,
        MetricKind.AVERAGE,
        MetricKind.DIVISION,
    }:
        denominator = _denominator(item)
        return denominator is not None and denominator > 0
    if kind in {MetricKind.AMOUNT, MetricKind.SCALAR}:
        population = _known_population(item)
        return population is not None and population >= 0
    return False


def assess_observation(item: Mapping[str, Any]) -> ObservationAssessment:
    kind = _metric_kind(item)
    declared, measured, value = _measurement(item, kind)
    number = _number(value) if measured else None
    is_zero = number == 0.0 if number is not None else False
    return ObservationAssessment(
        has_observation_date=has_observation_date(item),
        has_evidence=has_evidence(item),
        has_measured_fact=measured,
        invalid_explicit_observation=declared and not measured,
        is_zero=is_zero,
        zero_valid=not is_zero or _zero_is_valid(item, kind),
    )
