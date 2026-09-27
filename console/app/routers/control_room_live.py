from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated
from app.schemas.control_room_live import ControlRoomFreshnessResponse
from app.services.control_room.experience_freshness import (
    compute_experience_fingerprint,
)
from app.services.permissions import require_permission


router = APIRouter(tags=["Control Room"])


@router.get(
    "/experience/v2/freshness",
    response_model=ControlRoomFreshnessResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_experience_freshness(
    user: dict = Depends(require_authenticated),
) -> ControlRoomFreshnessResponse:
    return await compute_experience_fingerprint(user)


__all__ = ("router",)
