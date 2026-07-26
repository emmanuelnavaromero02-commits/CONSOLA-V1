from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from app.schemas.control_room_experience_actions import (
    ExperienceAction,
)
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


def resolve_business_experience_actions(
    item: Mapping[str, Any],
    identity: BusinessSurfaceIdentity,
    *,
    user: Mapping[str, Any],
    enabled_template_ids: Collection[str],
) -> list[ExperienceAction]:
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
        enabled = not stale and source_binding
        actions.append(
            ExperienceAction(
                action_handle=binding.binding_id,
                label=label,
                enabled=enabled,
                requires_approval=bool(template.get("requires_approval", True)),
                disabled_reason=(
                    None if enabled else _STALE_REASON if stale else _INCOMPLETE_REASON
                ),
            )
        )
    return actions


__all__ = ("resolve_business_experience_actions",)
