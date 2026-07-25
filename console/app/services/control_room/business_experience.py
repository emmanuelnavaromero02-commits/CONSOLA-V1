from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time

from app.schemas.control_room_surfaces import (
    EXPERIENCE_SCHEMA_VERSION,
    ControlRoomExperienceResponse,
    ExperienceDecision,
    ExperienceFact,
    ExperienceMetric,
    ExperienceSection,
)
from app.services.control_room.business_observation import (
    OBSERVATION_DATE_FIELDS,
    semantic_maps,
    semantic_states,
)
from app.services.control_room.business_projection import filter_business_items
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
    resolve_business_surface_identity,
    surface_section_title,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from app.services.control_room.business_surface_provenance import (
    surface_workflow_provenance_verified,
)
from app.services.control_room.business_semantic_slots import (
    MetricKind,
    resolve_metric_kind,
    resolve_semantic_slots,
)
from app.services.control_room.surface_snapshot import (
    SurfaceSnapshot,
    validate_snapshot_scope,
)


_DECISION_STATES = {"decision_created", "approved", "resolved"}
_SEVERITIES = {"critical", "high", "medium", "low"}


def _text(item: Mapping[str, object], *keys: str) -> str:
    for values in semantic_maps(item):
        for key in keys:
            value = values.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min, tzinfo=UTC)
    elif isinstance(value, str) and value.strip():
        text_value = value.strip()
        try:
            parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(text_value)
            except ValueError:
                return None
            parsed = datetime.combine(parsed_date, time.min, tzinfo=UTC)
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _observed_at(item: Mapping[str, object]) -> datetime | None:
    for values in semantic_maps(item):
        for field in OBSERVATION_DATE_FIELDS:
            if (parsed := _utc_datetime(values.get(field))) is not None:
                return parsed
    return None


def _visible_copy(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
    value: object,
    *,
    max_length: int,
) -> str | None:
    return classify_visible_business_copy(
        value,
        item=item,
        identity=identity,
        max_length=max_length,
    ).text


def _safe_structural_identity(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> bool:
    for value in (
        identity.domain,
        identity.cartridge_id,
        identity.module_id,
    ):
        result = classify_visible_business_copy(
            value,
            item=item,
            identity=identity,
            max_length=240,
        )
        if (
            not result.allowed
            and result.cause is not VisibleCopyCause.TECHNICAL_IDENTIFIER
        ):
            return False
    return True


def _metric(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> ExperienceMetric | None:
    metric_kind = resolve_metric_kind(item)
    if (
        not metric_kind.declared
        or not metric_kind.valid
        or metric_kind.value is MetricKind.UNKNOWN
    ):
        return None
    slots = resolve_semantic_slots(item)
    selected = slots.observed_value
    if metric_kind.value is MetricKind.COUNT and not slots.observed_value.declared:
        selected = slots.affected_count
    if not selected.valid or selected.value is None:
        return None
    raw_name = _text(item, "metric_name", "metric", "anomaly_type")
    name = _visible_copy(item, identity, raw_name, max_length=160)
    if name is None:
        name = metric_kind.value.value
    raw_unit = _text(item, "unit", "metric_unit", "currency")
    unit = _visible_copy(item, identity, raw_unit, max_length=64)
    return ExperienceMetric(
        name=name,
        kind=metric_kind.value.value,
        value=selected.value,
        unit=unit,
    )


def _decision(item: Mapping[str, object]) -> ExperienceDecision | None:
    if not surface_workflow_provenance_verified(item):
        return None
    decision_id = item.get("decision_id")
    if type(decision_id) is not int or decision_id <= 0:
        return None
    status = str(item.get("status") or "").strip().lower()
    if status not in _DECISION_STATES:
        return None
    return ExperienceDecision(reference=decision_id, status=status)


def _fact(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> ExperienceFact | None:
    observed_at = _observed_at(item)
    if observed_at is None:
        return None
    raw_kind = str(item.get("kind") or item.get("item_kind") or "").lower()
    kind = {
        "anomaly": "anomaly",
        "intelligence_signal": "signal",
        "agent_alert": "alert",
    }.get(raw_kind, "kpi")
    severity = str(item.get("severity") or "medium").lower()
    if severity not in _SEVERITIES:
        severity = "medium"
    title = _visible_copy(item, identity, item.get("title"), max_length=240)
    if title is None:
        return None
    entity_label = _visible_copy(
        item,
        identity,
        item.get("entity_label"),
        max_length=240,
    )
    return ExperienceFact(
        kind=kind,
        title=title,
        severity=severity,
        observed_at=observed_at,
        stale="stale" in semantic_states(item),
        entity_label=entity_label,
        metric=_metric(item, identity),
        decision=_decision(item),
    )


def _fact_sort_key(fact: ExperienceFact) -> tuple[float, str, str]:
    return (
        -fact.observed_at.timestamp(),
        fact.title,
        fact.model_dump_json(exclude_none=True),
    )


def build_business_experience(
    snapshot: SurfaceSnapshot,
) -> ControlRoomExperienceResponse:
    validate_snapshot_scope(snapshot)
    grouped: dict[
        BusinessSurfaceIdentity,
        tuple[set[str], list[ExperienceFact]],
    ] = {}
    for item in filter_business_items(snapshot.items):
        identity = resolve_business_surface_identity(item)
        if identity is None or not _safe_structural_identity(item, identity):
            continue
        fact = _fact(item, identity)
        if fact is None:
            continue
        titles, facts = grouped.setdefault(identity, (set(), []))
        titles.add(
            surface_section_title(
                item,
                identity,
                visible_copy=lambda value: _visible_copy(
                    item,
                    identity,
                    value,
                    max_length=240,
                ),
            )
        )
        facts.append(fact)

    sections: list[ExperienceSection] = []
    for identity, (titles, facts) in sorted(grouped.items()):
        facts.sort(key=_fact_sort_key)
        if facts:
            sections.append(
                ExperienceSection(
                    id=identity.section_id,
                    cartridge_id=identity.cartridge_id,
                    module_id=identity.module_id,
                    title=min(titles, key=lambda value: (value.casefold(), value)),
                    domain=identity.domain,
                    facts=facts,
                )
            )
    return ControlRoomExperienceResponse(
        schema_version=EXPERIENCE_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        scope=snapshot.scope,
        sections=sections,
    )


__all__ = ("build_business_experience",)
