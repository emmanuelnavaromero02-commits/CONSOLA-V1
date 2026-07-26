from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
)
from app.services.control_room.business_action_resolution import (
    authorized_explicit_action_bindings,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot


@dataclass(frozen=True)
class ResolvedActionHandle:
    item_id: str
    template_id: str
    binding_id: str


async def resolve_business_action_handle(
    user: Mapping[str, Any], action_handle: str
) -> ResolvedActionHandle:
    snapshot = await collect_surface_snapshot(user)
    enabled = await load_enabled_action_template_ids(user)
    matches: list[ResolvedActionHandle] = []
    for item in snapshot.items:
        for binding in authorized_explicit_action_bindings(
            item, user, enabled_template_ids=enabled
        ):
            if binding.binding_id != action_handle:
                continue
            item_id = item.get("id") or item.get("item_id")
            if isinstance(item_id, str) and item_id:
                matches.append(
                    ResolvedActionHandle(
                        item_id=item_id,
                        template_id=binding.template_id,
                        binding_id=binding.binding_id,
                    )
                )
    if len(matches) != 1:
        raise HTTPException(404, "action binding not found")
    return matches[0]


__all__ = ("ResolvedActionHandle", "resolve_business_action_handle")
