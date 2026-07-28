from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_cartridge_scope import (
    business_cartridge_allowed,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_explicit_action_binding import (
    VerifiedActionBinding,
    verified_explicit_action_bindings,
)
from app.services.control_room.business_source_scope import context_scope, scope_matches
from app.services.permissions import has_permission


def authorized_explicit_action_bindings(
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    enabled_template_ids: Collection[str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[VerifiedActionBinding, ...]:
    tenant_id, workspace_id = context_scope(user)
    cartridge_id = str(item.get("cartridge") or item.get("cartridge_id") or "")
    if not (
        has_permission(dict(user), "control_room.write")
        and scope_matches(item, tenant_id=tenant_id, workspace_id=workspace_id)
        and business_cartridge_allowed(user, cartridge_id, allow_platform=True)
        and classify_business_item(item).eligible
        and has_evidence(item)
    ):
        return ()
    enabled = set(enabled_template_ids) if enabled_template_ids is not None else None
    return tuple(
        binding
        for binding in verified_explicit_action_bindings(item, clock=clock)
        if enabled is None or binding.template_id in enabled
    )


def require_explicit_action_template(
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    template_id: str | None,
    binding_id: str | None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if not template_id or not binding_id:
        raise HTTPException(422, "template_id and binding_id are required")
    binding = next(
        (
            value
            for value in authorized_explicit_action_bindings(item, user, clock=clock)
            if value.template_id == template_id and value.binding_id == binding_id
        ),
        None,
    )
    template = ACTION_TEMPLATES.get(template_id)
    if binding is None or template is None:
        raise HTTPException(404, "action binding not found")
    return dict(template)


def single_explicit_action_binding(
    item: Mapping[str, Any], user: Mapping[str, Any]
) -> VerifiedActionBinding:
    bindings = authorized_explicit_action_bindings(item, user)
    if not bindings:
        raise HTTPException(404, "action binding not found")
    if len(bindings) != 1:
        raise HTTPException(409, "automatic mode requires one explicit action binding")
    return bindings[0]


__all__ = (
    "authorized_explicit_action_bindings",
    "require_explicit_action_template",
    "single_explicit_action_binding",
)
