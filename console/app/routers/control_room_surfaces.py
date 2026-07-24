from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated
from app.schemas.control_room_surfaces import (
    ControlRoomDiagnosticsResponse,
    ControlRoomExperienceResponse,
)
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services.permissions import require_permission


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
