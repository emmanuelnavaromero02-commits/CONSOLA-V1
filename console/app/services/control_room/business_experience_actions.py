from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from app.schemas.control_room_experience_actions import (
    ExperienceAction,
    ExperienceActionPrerequisite,
)
from app.services.control_room.business_action_binding import preview_action_endpoint
from app.services.control_room.business_action_authority import (
    action_item_is_current,
    action_item_is_stale,
    action_source_binding_complete,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_resolution import (
    authorized_explicit_action_bindings,
)
from app.services.control_room.business_surface_identity import BusinessSurfaceIdentity
from app.services.control_room.business_visible_copy import (
    classify_visible_business_copy,
)


_STALE_REASON = "Actualiza los datos antes de continuar."
_INCOMPLETE_REASON = "Completa los datos requeridos antes de continuar."


def _prerequisites(
    *,
    fresh: bool,
    source_binding: bool,
) -> list[ExperienceActionPrerequisite]:
    states = {
        "business_eligible": True,
        "evidence": True,
        "scope": True,
        "template": True,
        "permission": True,
        "non_terminal": True,
        "freshness": fresh,
        "source_binding": source_binding,
    }
    return [
        ExperienceActionPrerequisite(code=code, satisfied=satisfied)
        for code, satisfied in states.items()
    ]


def resolve_business_experience_actions(
    item: Mapping[str, Any],
    identity: BusinessSurfaceIdentity,
    *,
    user: Mapping[str, Any],
    enabled_template_ids: Collection[str],
) -> list[ExperienceAction]:
    item_id = str(item.get("id") or "")
    if not action_item_is_current(item, operation="preview"):
        return []
    bindings = authorized_explicit_action_bindings(
        item,
        user,
        enabled_template_ids=enabled_template_ids,
    )
    stale = action_item_is_stale(item)
    source_binding = action_source_binding_complete(item)
    if stale or not source_binding:
        bindings = bindings[:1]

    actions: list[ExperienceAction] = []
    for binding in bindings:
        template_id = binding.template_id
        template = ACTION_TEMPLATES[template_id]
        label = classify_visible_business_copy(
            template.get("label"),
            item=item,
            identity=identity,
            max_length=120,
        ).text
        if label is None:
            continue
        prerequisites = _prerequisites(
            fresh=not stale,
            source_binding=source_binding,
        )
        enabled = all(value.satisfied for value in prerequisites)
        actions.append(
            ExperienceAction(
                item_id=str(item_id),
                template_id=template_id,
                label=label,
                operation="preview",
                enabled=enabled,
                requires_approval=bool(template.get("requires_approval", True)),
                prerequisites=prerequisites,
                disabled_reason=(
                    None if enabled else _STALE_REASON if stale else _INCOMPLETE_REASON
                ),
                method="POST",
                endpoint=preview_action_endpoint(str(item_id)),
                binding=binding.public_values(),
            )
        )
    return actions


__all__ = ("resolve_business_experience_actions",)
