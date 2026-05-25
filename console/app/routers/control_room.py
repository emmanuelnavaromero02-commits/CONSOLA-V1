from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.dependencies import require_authenticated
from app.services import control_room_service
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/control-room", tags=["Control Room"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/summary", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_summary(user: dict = Depends(require_authenticated)):
    return await control_room_service.summary(user)


@router.get("/dashboard", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_dashboard(user: dict = Depends(require_authenticated)):
    return await control_room_service.dashboard(user)


@router.get("/anomalies", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_anomalies(user: dict = Depends(require_authenticated)):
    return await control_room_service.list_anomalies(user)


@router.get("/items/{item_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_detail(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_item(item_id, user)


@router.get("/anomalies/{anomaly_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_anomaly_detail(anomaly_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_anomaly(anomaly_id, user)


@router.post(
    "/anomalies/{anomaly_id}/decision",
    dependencies=[Depends(require_csrf), Depends(require_permission("workspace.access"))],
)
async def control_room_create_decision(
    anomaly_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.create_decision_for_anomaly(
        anomaly_id,
        user,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/decision",
    dependencies=[Depends(require_csrf), Depends(require_permission("workspace.access"))],
)
async def control_room_create_item_decision(
    item_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.create_decision_for_item(
        item_id,
        user,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/anomalies/{anomaly_id}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("workspace.access"))],
)
async def control_room_approve(
    anomaly_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    decision_id = None
    if isinstance(body, dict) and body.get("decision_id") is not None:
        decision_id = int(body["decision_id"])
    return await control_room_service.approve_anomaly(
        anomaly_id,
        user,
        decision_id=decision_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("workspace.access"))],
)
async def control_room_approve_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    decision_id = None
    if isinstance(body, dict) and body.get("decision_id") is not None:
        decision_id = int(body["decision_id"])
    return await control_room_service.approve_item(
        item_id,
        user,
        decision_id=decision_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/dismiss",
    dependencies=[Depends(require_csrf), Depends(require_permission("workspace.access"))],
)
async def control_room_dismiss_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    reason = body.get("reason") if isinstance(body, dict) else None
    return await control_room_service.dismiss_item(
        item_id,
        user,
        reason=str(reason or ""),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/reopen",
    dependencies=[Depends(require_csrf), Depends(require_permission("workspace.access"))],
)
async def control_room_reopen_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    reason = body.get("reason") if isinstance(body, dict) else None
    return await control_room_service.reopen_item(
        item_id,
        user,
        reason=str(reason or ""),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
