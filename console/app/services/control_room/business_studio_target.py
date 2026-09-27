from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.schemas.control_room_direct_actions import StudioTargetResponse
from app.services.control_room.business_action_authority_policy import (
    OPEN_IN_STUDIO_TEMPLATE_ID,
)
from app.services.control_room.business_action_direct_contract import studio_href
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)


async def resolve_studio_target(
    user: Mapping[str, Any], *, action_handle: str
) -> StudioTargetResponse:
    resolved = await resolve_business_action_handle(
        user,
        action_handle,
        allowed_template_ids=frozenset({OPEN_IN_STUDIO_TEMPLATE_ID}),
    )
    try:
        href = studio_href(str(resolved.cartridge_id or ""))
    except ValueError:
        raise HTTPException(404, "action binding not found") from None
    return StudioTargetResponse(action_handle=action_handle, href=href)


__all__ = ("resolve_studio_target",)
