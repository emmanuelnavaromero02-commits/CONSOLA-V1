from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated
from app.schemas.pipeline_automations import AutomationsResponse
from app.services.permissions import require_permission
from app.services.pipeline_automations import list_automations


router = APIRouter(prefix="/api/pipelines", tags=["Pipelines"])


@router.get(
    "/automations",
    response_model=AutomationsResponse,
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def pipeline_automations(
    user: dict = Depends(require_authenticated),
) -> AutomationsResponse:
    return await list_automations(user)


__all__ = ("router",)
