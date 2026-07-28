from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated
from app.schemas.control_room_surfaces import (
    ControlRoomDiagnosticsResponse,
    ControlRoomExperienceResponse,
)
from app.schemas.control_room_experience_actions import (
    ControlRoomExperienceV2Response,
)
from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
)
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services.permissions import has_permission, require_permission


router = APIRouter(tags=["Control Room"])


@router.get(
    "/experience",
    response_model=ControlRoomExperienceResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_experience(
    user: dict = Depends(require_authenticated),
) -> ControlRoomExperienceResponse:
    snapshot = await collect_surface_snapshot(user)
    return build_business_experience(snapshot)


@router.get(
    "/experience/v2",
    response_model=ControlRoomExperienceV2Response,
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_experience_v2(
    user: dict = Depends(require_authenticated),
) -> ControlRoomExperienceV2Response:
    snapshot = await collect_surface_snapshot(user)
    enabled_template_ids = (
        await load_enabled_action_template_ids(user)
        if has_permission(user, "control_room.write")
        else frozenset()
    )
    return build_business_experience_v2(
        snapshot,
        user=user,
        enabled_template_ids=enabled_template_ids,
    )


@router.get(
    "/diagnostics",
    response_model=ControlRoomDiagnosticsResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_diagnostics(
    user: dict = Depends(require_authenticated),
) -> ControlRoomDiagnosticsResponse:
    snapshot = await collect_surface_snapshot(user)
    return build_operational_diagnostics(snapshot)


__all__ = ("router",)
