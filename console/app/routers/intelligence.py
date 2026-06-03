from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from app.dependencies import require_authenticated
from app.services import intelligence_engine
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/intelligence", tags=["Intelligence"])


@router.get("/signals", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_signals(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.list_signals(user, limit=limit)


@router.get("/signals/{signal_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_signal_detail(
    signal_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.get_signal(user, signal_id)


@router.post(
    "/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_run(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.run_intelligence(user, body if isinstance(body, dict) else {})


@router.get("/external/sources", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_external_sources(
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.list_sources(user)


@router.patch(
    "/external/sources/{source_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_update_external_source(
    source_id: str,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.patch_source(user, source_id, body if isinstance(body, dict) else {})


@router.post(
    "/external/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_external_run(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.run_sources(user, body if isinstance(body, dict) else {})


@router.post(
    "/signals/{signal_id}/options/{option_id}/select",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_select_option(
    signal_id: str,
    option_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.select_option(user, signal_id, option_id)


@router.post(
    "/signals/{signal_id}/outcome",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_record_outcome(
    signal_id: str,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.record_outcome(user, signal_id, body if isinstance(body, dict) else {})
