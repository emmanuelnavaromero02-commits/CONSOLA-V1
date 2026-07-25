from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from app.schemas.control_room_experience_actions import (
    ExperienceAction,
    ExperienceActionPrerequisite,
)
from app.services.control_room.business_action_binding import (
    preview_action_endpoint,
    valid_action_item_id,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_templates import (
    template_ids_for_business_item,
)
from app.services.control_room.business_cartridge_scope import (
    business_cartridge_allowed,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_observation import semantic_states
from app.services.control_room.business_source_scope import context_scope, scope_matches
from app.services.control_room.business_surface_identity import BusinessSurfaceIdentity
from app.services.control_room.business_visible_copy import (
    classify_visible_business_copy,
)
from app.services.control_room.business_workflow_state import (
    TERMINAL_EXECUTION_STATUSES,
)
from app.services.permissions import has_permission


_TERMINAL_ITEM_STATUSES = frozenset({"dismissed", "resolved"})
_STALE_REASON = "Actualiza los datos antes de continuar."
_INCOMPLETE_REASON = "Completa los datos requeridos antes de continuar."


def _is_terminal(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    execution = str(item.get("execution_status") or "").strip().lower()
    return status in _TERMINAL_ITEM_STATUSES or execution in TERMINAL_EXECUTION_STATUSES


def _source_binding_complete(item: Mapping[str, Any]) -> bool:
    return bool(str(item.get("source_dataset") or "").strip()) and bool(
        str(item.get("entity_id") or "").strip()
    )


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
    item_id = item.get("id")
    tenant_id, workspace_id = context_scope(user)
    cartridge_id = str(item.get("cartridge") or item.get("cartridge_id") or "")
    if not (
        valid_action_item_id(item_id)
        and has_permission(dict(user), "control_room.write")
        and scope_matches(item, tenant_id=tenant_id, workspace_id=workspace_id)
        and business_cartridge_allowed(user, cartridge_id, allow_platform=True)
        and classify_business_item(item).eligible
        and not _is_terminal(item)
    ):
        return []

    available_ids = set(enabled_template_ids)
    template_ids = [
        template_id
        for template_id in template_ids_for_business_item(item)
        if template_id in available_ids and template_id in ACTION_TEMPLATES
    ]
    stale = "stale" in semantic_states(item)
    source_binding = _source_binding_complete(item)
    if stale or not source_binding:
        template_ids = template_ids[:1]

    actions: list[ExperienceAction] = []
    for template_id in template_ids:
        template = ACTION_TEMPLATES[template_id]
        if str(template.get("cartridge_id") or "") not in {"platform", cartridge_id}:
            continue
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
                    None
                    if enabled
                    else _STALE_REASON
                    if stale
                    else _INCOMPLETE_REASON
                ),
                method="POST",
                endpoint=preview_action_endpoint(str(item_id)),
            )
        )
    return actions


__all__ = ("resolve_business_experience_actions",)
