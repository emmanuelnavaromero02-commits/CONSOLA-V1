from __future__ import annotations

from collections.abc import Collection, Mapping

from app.schemas.control_room_experience_actions import (
    EXPERIENCE_ACTIONS_SCHEMA_VERSION,
    ControlRoomExperienceV2Response,
    ExperienceAction,
    ExperienceFactV2,
    ExperienceSectionV2,
)
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


def build_business_experience_v2(
    snapshot: SurfaceSnapshot,
    *,
    user: Mapping[str, object],
    enabled_template_ids: Collection[str],
    actions_by_item: Mapping[str, Collection[ExperienceAction]] | None = None,
) -> ControlRoomExperienceV2Response:
    validate_snapshot_scope(snapshot)
    grouped: dict[
        BusinessSurfaceIdentity,
        tuple[set[str], list[ExperienceFactV2]],
    ] = {}
    for item in filter_business_items(snapshot.items):
        identity = resolve_bounded_business_surface_identity(
            item,
            max_length=MAX_STRUCTURAL_IDENTITY_LENGTH,
        )
        if identity is None or not structural_identity_is_safe(item, identity):
            continue
        fact = project_experience_fact(item, identity)
        if fact is None:
            continue
        item_id = str(item.get("id") or item.get("item_id") or "")
        actions = (
            list(actions_by_item.get(item_id, ()))
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
    return ControlRoomExperienceV2Response(
        schema_version=EXPERIENCE_ACTIONS_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        sections=sections,
    )


__all__ = ("build_business_experience_v2",)
