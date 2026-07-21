from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.control_room.business_observation_codec import semantic_surfaces


class MetricKind(StrEnum):
    COUNT = "count"
    RATE = "rate"
    PERCENTAGE = "percentage"
    AVERAGE = "average"
    DIVISION = "division"
    AMOUNT = "amount"
    SCALAR = "scalar"
    UNKNOWN = "unknown"


class SemanticSlot(StrEnum):
    OBSERVED_VALUE = "observed_value"
    NUMERATOR = "numerator"
    DENOMINATOR = "denominator"
    POPULATION = "population"
    AFFECTED_COUNT = "affected_count"
    SOURCE_ROWS = "source_rows"


SLOT_ALIASES: Mapping[SemanticSlot, tuple[str, ...]] = {
    SemanticSlot.OBSERVED_VALUE: (
        "observed_value",
        "metric_value",
        "actual_value",
        "value",
    ),
    SemanticSlot.NUMERATOR: ("numerator", "numerator_count"),
    SemanticSlot.DENOMINATOR: ("denominator", "denominator_count"),
    SemanticSlot.POPULATION: (
        "population",
        "population_count",
        "sample_count",
        "total_count",
    ),
    SemanticSlot.AFFECTED_COUNT: ("affected_count",),
    SemanticSlot.SOURCE_ROWS: ("source_row_count",),
}
METRIC_KIND_FIELDS = (
    "metric_type",
    "metric_kind",
    "aggregation_type",
    "value_type",
)
OBSERVATION_FLAG_FIELDS = (
    "value_observed",
    "is_observed",
    "observation_valid",
)
_METRIC_KIND_ALIASES = {
    "counter": MetricKind.COUNT,
    "number": MetricKind.COUNT,
    "percent": MetricKind.PERCENTAGE,
    "ratio": MetricKind.RATE,
    "mean": MetricKind.AVERAGE,
    "currency": MetricKind.AMOUNT,
    "numeric": MetricKind.SCALAR,
}


@dataclass(frozen=True)
class ResolvedNumber:
    slot: SemanticSlot
    declared: bool
    valid: bool
    value: float | None
    contradictory: bool = False


@dataclass(frozen=True)
class ResolvedMetricKind:
    declared: bool
    valid: bool
    value: MetricKind
    contradictory: bool = False


@dataclass(frozen=True)
class ResolvedObservationFlag:
    declared: bool
    valid: bool
    value: bool | None
    contradictory: bool = False


@dataclass(frozen=True)
class SemanticSlots:
    observed_value: ResolvedNumber
    numerator: ResolvedNumber
    denominator: ResolvedNumber
    population: ResolvedNumber
    affected_count: ResolvedNumber
    source_rows: ResolvedNumber

    @property
    def values(self) -> tuple[ResolvedNumber, ...]:
        return (
            self.observed_value,
            self.numerator,
            self.denominator,
            self.population,
            self.affected_count,
            self.source_rows,
        )

    @property
    def declared(self) -> bool:
        return any(value.declared for value in self.values)

    @property
    def valid(self) -> bool:
        return all(value.valid for value in self.values)


def semantic_maps(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return semantic_surfaces(item)


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _resolve_number(
    item: Mapping[str, Any],
    slot: SemanticSlot,
    aliases: tuple[str, ...],
) -> ResolvedNumber:
    raw_values = [
        values.get(key)
        for values in semantic_maps(item)
        for key in aliases
        if key in values
    ]
    if not raw_values:
        return ResolvedNumber(slot, False, True, None)
    numbers = [finite_number(value) for value in raw_values]
    valid_numbers = [number for number in numbers if number is not None]
    contradictory = len(set(valid_numbers)) > 1
    valid = len(valid_numbers) == len(raw_values) and not contradictory
    return ResolvedNumber(
        slot,
        True,
        valid,
        valid_numbers[0] if valid else None,
        contradictory,
    )


def resolve_semantic_slots(item: Mapping[str, Any]) -> SemanticSlots:
    metric_kind = resolve_metric_kind(item)
    resolved = {}
    for slot in SemanticSlot:
        aliases = SLOT_ALIASES[slot]
        if (
            slot is SemanticSlot.OBSERVED_VALUE
            and metric_kind.valid
            and (metric_kind.value is MetricKind.COUNT or not metric_kind.declared)
        ):
            aliases = (*aliases, "count")
        resolved[slot] = _resolve_number(item, slot, aliases)
    return SemanticSlots(
        observed_value=resolved[SemanticSlot.OBSERVED_VALUE],
        numerator=resolved[SemanticSlot.NUMERATOR],
        denominator=resolved[SemanticSlot.DENOMINATOR],
        population=resolved[SemanticSlot.POPULATION],
        affected_count=resolved[SemanticSlot.AFFECTED_COUNT],
        source_rows=resolved[SemanticSlot.SOURCE_ROWS],
    )


def _normalized_metric_kind(value: Any) -> MetricKind | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in _METRIC_KIND_ALIASES:
        return _METRIC_KIND_ALIASES[normalized]
    try:
        return MetricKind(normalized)
    except ValueError:
        return None


def resolve_metric_kind(item: Mapping[str, Any]) -> ResolvedMetricKind:
    raw_values = [
        values.get(key)
        for values in semantic_maps(item)
        for key in METRIC_KIND_FIELDS
        if key in values
    ]
    if not raw_values:
        return ResolvedMetricKind(False, True, MetricKind.UNKNOWN)
    kinds = [_normalized_metric_kind(value) for value in raw_values]
    valid_kinds = [kind for kind in kinds if kind is not None]
    contradictory = len(set(valid_kinds)) > 1
    valid = (
        len(valid_kinds) == len(raw_values)
        and not contradictory
        and MetricKind.UNKNOWN not in valid_kinds
    )
    return ResolvedMetricKind(
        True,
        valid,
        valid_kinds[0] if valid else MetricKind.UNKNOWN,
        contradictory,
    )


def resolve_observation_flag(item: Mapping[str, Any]) -> ResolvedObservationFlag:
    raw_values = [
        values.get(key)
        for values in semantic_maps(item)
        for key in OBSERVATION_FLAG_FIELDS
        if key in values
    ]
    if not raw_values:
        return ResolvedObservationFlag(False, True, None)
    bool_values = [value for value in raw_values if isinstance(value, bool)]
    contradictory = len(set(bool_values)) > 1
    valid = len(bool_values) == len(raw_values) and not contradictory
    return ResolvedObservationFlag(
        True,
        valid,
        bool_values[0] if valid else None,
        contradictory,
    )


__all__ = (
    "MetricKind",
    "ResolvedMetricKind",
    "ResolvedNumber",
    "ResolvedObservationFlag",
    "SemanticSlot",
    "SemanticSlots",
    "finite_number",
    "resolve_metric_kind",
    "resolve_observation_flag",
    "resolve_semantic_slots",
    "semantic_maps",
)
