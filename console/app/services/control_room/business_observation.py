from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.services.control_room.business_evidence import EVIDENCE_FIELDS, has_evidence
from app.services.control_room.business_observation_codec import INVALID_ENVELOPE_FIELD
from app.services.control_room.business_semantic_slots import (
    MetricKind,
    ResolvedNumber,
    SemanticSlots,
    finite_number,
    resolve_metric_kind,
    resolve_observation_flag,
    resolve_semantic_slots,
    semantic_maps,
)


SUCCESSFUL_EVALUATION_STATES = frozenset(
    {"complete", "gold_ready", "materialized", "ok", "ready", "success"}
)
OBSERVATION_DATE_FIELDS = (
    "observed_at",
    "observation_date",
    "detected_at",
    "as_of",
)
_OBSERVED_VALUE_KINDS = frozenset(
    {
        MetricKind.RATE,
        MetricKind.PERCENTAGE,
        MetricKind.AVERAGE,
        MetricKind.DIVISION,
        MetricKind.AMOUNT,
        MetricKind.SCALAR,
    }
)


@dataclass(frozen=True)
class ObservationAssessment:
    declared: bool
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


def _first_value(*slots: ResolvedNumber) -> float | None:
    for slot in slots:
        if slot.declared:
            return slot.value
    return None


def _selected_value(
    kind: MetricKind,
    slots: SemanticSlots,
) -> float | None:
    if kind is MetricKind.COUNT:
        return _first_value(slots.observed_value, slots.affected_count)
    if kind in _OBSERVED_VALUE_KINDS:
        return _first_value(slots.observed_value)
    return None


def _required_value_missing(
    kind: MetricKind,
    slots: SemanticSlots,
) -> bool:
    if kind is MetricKind.COUNT:
        return not (slots.observed_value.declared or slots.affected_count.declared)
    return not slots.observed_value.declared


def _measurement(item: Mapping[str, Any]) -> tuple[bool, bool, float | None]:
    invalid_envelope = any(
        values.get(INVALID_ENVELOPE_FIELD) is True for values in semantic_maps(item)
    )
    metric_kind = resolve_metric_kind(item)
    slots = resolve_semantic_slots(item)
    observation_flag = resolve_observation_flag(item)
    kind = metric_kind.value if metric_kind.valid else MetricKind.UNKNOWN
    quantitative_declared = slots.declared or observation_flag.declared
    known_metric = (
        metric_kind.declared and metric_kind.valid and kind is not MetricKind.UNKNOWN
    )
    value = _selected_value(kind, slots) if known_metric else None
    required_value_missing = known_metric and _required_value_missing(kind, slots)
    metric_contract_invalid = quantitative_declared and not known_metric
    declared = (
        quantitative_declared
        or metric_kind.declared
        or required_value_missing
        or invalid_envelope
        or not metric_kind.valid
        or not slots.valid
    )
    invalid = (
        not metric_kind.valid
        or invalid_envelope
        or not slots.valid
        or not observation_flag.valid
        or (observation_flag.declared and observation_flag.value is not True)
        or required_value_missing
        or metric_contract_invalid
    )
    if invalid or value is None:
        return declared, False, None
    return declared, True, value


def _denominator(slots: SemanticSlots) -> float | None:
    return _first_value(slots.denominator, slots.population)


def _zero_is_valid(item: Mapping[str, Any], kind: MetricKind) -> bool:
    states = semantic_states(item)
    if not states & SUCCESSFUL_EVALUATION_STATES or not has_observation_date(item):
        return False
    slots = resolve_semantic_slots(item)
    if not slots.valid:
        return False
    if kind is MetricKind.COUNT:
        population = slots.population.value if slots.population.declared else None
        return population is not None and population >= 0
    if kind in {
        MetricKind.RATE,
        MetricKind.PERCENTAGE,
        MetricKind.AVERAGE,
        MetricKind.DIVISION,
    }:
        denominator = _denominator(slots)
        return denominator is not None and denominator > 0
    if kind in {MetricKind.AMOUNT, MetricKind.SCALAR}:
        return True
    return False


def _nonempty_field(item: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    containers = [item]
    for key in ("details", "metadata"):
        value = item.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    return any(
        str(values.get(field) or "").strip()
        for values in containers
        for field in fields
    )


def has_qualitative_anomaly_observation(item: Mapping[str, Any]) -> bool:
    kinds = {
        str(values.get(field) or "").strip().lower()
        for values in semantic_maps(item)
        for field in ("kind", "item_kind")
        if str(values.get(field) or "").strip()
    }
    return bool(
        "anomaly" in kinds
        and has_observation_date(item)
        and has_evidence(item)
        and _nonempty_field(item, ("source_dataset", "dataset", "gold_table"))
        and _nonempty_field(item, ("source_system", "cartridge", "connector_id"))
        and _nonempty_field(item, ("entity_id", "anomaly_id", "signal_id"))
    )


def assess_observation(item: Mapping[str, Any]) -> ObservationAssessment:
    metric_kind = resolve_metric_kind(item)
    kind = metric_kind.value if metric_kind.valid else MetricKind.UNKNOWN
    declared, measured, value = _measurement(item)
    number = finite_number(value) if measured else None
    is_zero = number == 0.0 if number is not None else False
    return ObservationAssessment(
        declared=declared,
        has_observation_date=has_observation_date(item),
        has_evidence=has_evidence(item),
        has_measured_fact=measured,
        invalid_explicit_observation=declared and not measured,
        is_zero=is_zero,
        zero_valid=not is_zero or _zero_is_valid(item, kind),
    )


__all__ = (
    "EVIDENCE_FIELDS",
    "MetricKind",
    "OBSERVATION_DATE_FIELDS",
    "ObservationAssessment",
    "SUCCESSFUL_EVALUATION_STATES",
    "assess_observation",
    "has_evidence",
    "has_qualitative_anomaly_observation",
    "has_observation_date",
    "semantic_maps",
    "semantic_states",
)
