from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime

from app.schemas.control_room_experience_actions import (
    EXPERIENCE_ACTIONS_SCHEMA_VERSION,
    MAX_EXPERIENCE_EXCEPTIONS,
    ControlRoomExperienceV2Response,
    ExperienceAction,
    ExperienceExceptionV2,
    ExperienceFactV2,
    ExperienceSectionV2,
)
from app.services.control_room.business_access import actor_id
from app.services.control_room.business_experience import (
    experience_fact_sort_key,
    project_experience_fact,
)
from app.services.control_room.business_experience_actions import (
    resolve_business_experience_actions,
)
from app.services.control_room.business_experience_copy import (
    MAX_STRUCTURAL_IDENTITY_LENGTH,
    structural_identity_is_safe,
    visible_business_copy,
)
from app.services.control_room.business_experience_narrative import (
    project_experience_narrative,
)
from app.services.control_room.business_exception_resolution import (
    ExceptionResolution,
)
from app.services.control_room.business_projection import filter_business_items
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
    resolve_bounded_business_surface_identity,
    surface_section_title,
)
from app.services.control_room.surface_snapshot import (
    SurfaceSnapshot,
    validate_snapshot_scope,
)


_CLOSED_STATUSES = frozenset({"dismissed", "resolved"})
_OLDEST = datetime.min.replace(tzinfo=UTC)


def _status(item: Mapping[str, object]) -> str:
    return str(item.get("status") or "open").strip().lower()


def _exception_sort_key(exception: ExperienceExceptionV2) -> tuple[float, str]:
    approved_at = exception.approved_at or _OLDEST
    return (-approved_at.timestamp(), exception.title)


def _approved_exception(
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
    resolution: ExceptionResolution,
    *,
    user: Mapping[str, object],
    actions: Collection[ExperienceAction],
) -> ExperienceExceptionV2 | None:
    fact = project_experience_fact(item, identity)
    if fact is None:
        return None
    viewer = actor_id(user.get("id"))
    return ExperienceExceptionV2(
        title=fact.title,
        entity_label=fact.entity_label,
        observed_at=fact.observed_at,
        approved_at=resolution.approved_at,
        reason=(
            visible_business_copy(item, identity, resolution.reason, max_length=500)
            if resolution.reason
            else None
        ),
        approved_by_you=viewer is not None and resolution.actor_user_id == viewer,
        actions=[action for action in actions if action.kind == "exception_reopen"][:1],
    )


def build_business_experience_v2(
    snapshot: SurfaceSnapshot,
    *,
    user: Mapping[str, object],
    enabled_template_ids: Collection[str],
    actions_by_item: Mapping[str, Collection[ExperienceAction]] | None = None,
    exception_resolutions: Mapping[str, ExceptionResolution] | None = None,
) -> ControlRoomExperienceV2Response:
    validate_snapshot_scope(snapshot)
    grouped: dict[
        BusinessSurfaceIdentity,
        tuple[set[str], list[ExperienceFactV2]],
    ] = {}
    exceptions: list[ExperienceExceptionV2] = []
    for item in filter_business_items(snapshot.items):
        identity = resolve_bounded_business_surface_identity(
            item,
            max_length=MAX_STRUCTURAL_IDENTITY_LENGTH,
        )
        if identity is None or not structural_identity_is_safe(item, identity):
            continue
        item_id = str(item.get("id") or item.get("item_id") or "")
        status = _status(item)
        if status in _CLOSED_STATUSES:
            resolution = (exception_resolutions or {}).get(item_id)
            if status == "dismissed" and resolution is not None:
                exception = _approved_exception(
                    item,
                    identity,
                    resolution,
                    user=user,
                    actions=(
                        list(actions_by_item.get(item_id, ()))
                        if actions_by_item is not None
                        else []
                    ),
                )
                if exception is not None:
                    exceptions.append(exception)
            continue
        fact = project_experience_fact(item, identity)
        if fact is None:
            continue
        actions = (
            [
                action
                for action in actions_by_item.get(item_id, ())
                if action.kind != "exception_reopen"
            ]
            if actions_by_item is not None
            else resolve_business_experience_actions(
                item,
                identity,
                user=user,
                enabled_template_ids=enabled_template_ids,
            )
        )
        fact_v2 = ExperienceFactV2.model_validate(
            {
                **fact.model_dump(),
                "actions": actions,
                "narrative": project_experience_narrative(
                    snapshot.narratives.get(item_id), item, identity
                ),
            }
        )
        titles, facts = grouped.setdefault(identity, (set(), []))
        titles.add(
            surface_section_title(
                item,
                identity,
                visible_copy=lambda value: visible_business_copy(
                    item,
                    identity,
                    value,
                    max_length=240,
                ),
            )
        )
        facts.append(fact_v2)

    sections: list[ExperienceSectionV2] = []
    for identity, (titles, facts) in sorted(grouped.items()):
        facts.sort(key=experience_fact_sort_key)
        if facts:
            sections.append(
                ExperienceSectionV2(
                    title=min(titles, key=lambda value: (value.casefold(), value)),
                    facts=facts,
                )
            )
    exceptions.sort(key=_exception_sort_key)
    return ControlRoomExperienceV2Response(
        schema_version=EXPERIENCE_ACTIONS_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        sections=sections,
        exceptions=exceptions[:MAX_EXPERIENCE_EXCEPTIONS],
    )


__all__ = ("build_business_experience_v2",)
