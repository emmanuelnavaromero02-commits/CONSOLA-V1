from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request

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


@router.get("/items/{item_id}/impact", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_impact(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_item_impact(item_id, user)


@router.get("/items/{item_id}/activity", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_activity(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_item_activity(item_id, user)


@router.post(
    "/items/{item_id}/step",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_record_item_step(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    step_id = body.get("step_id") if isinstance(body, dict) else None
    note = body.get("note") if isinstance(body, dict) else None
    control_id = body.get("control_id") if isinstance(body, dict) else None
    return await control_room_service.record_item_step(
        item_id,
        str(step_id or ""),
        user,
        note=str(note or ""),
        control_id=str(control_id or ""),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/lessons",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_item_lesson(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.create_item_lesson(
        item_id,
        body if isinstance(body, dict) else {},
        user,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.get("/anomalies/{anomaly_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_anomaly_detail(anomaly_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_anomaly(anomaly_id, user)


@router.post(
    "/anomalies/{anomaly_id}/decision",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    "/items/{item_id}/option",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_select_item_option(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    option_id = body.get("option_id") if isinstance(body, dict) else None
    return await control_room_service.select_item_option(
        item_id,
        str(option_id or ""),
        user,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/action-preview",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_action_preview(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    template_id = body.get("template_id") if isinstance(body, dict) else None
    return await control_room_service.action_preview(
        item_id,
        user,
        template_id=str(template_id) if template_id else None,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/action-dry-run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_action_dry_run(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    template_id = body.get("template_id") if isinstance(body, dict) else None
    return await control_room_service.action_dry_run(
        item_id,
        user,
        template_id=str(template_id) if template_id else None,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/items/{item_id}/execute",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_execute_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    template_id = body.get("template_id") if isinstance(body, dict) else None
    return await control_room_service.execute_item(
        item_id,
        user,
        template_id=str(template_id) if template_id else None,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/anomalies/{anomaly_id}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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


@router.get("/thresholds", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_thresholds(user: dict = Depends(require_authenticated)):
    return await control_room_service.list_thresholds(user)


@router.post(
    "/thresholds",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_upsert_threshold(
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.upsert_threshold(
        body if isinstance(body, dict) else {},
        user,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.patch(
    "/thresholds",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_patch_threshold(
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.upsert_threshold(
        body if isinstance(body, dict) else {},
        user,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.get("/lessons", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_lessons(
    cartridge_id: str | None = Query(default=None),
    anomaly_type: str | None = Query(default=None),
    item_id: str | None = Query(default=None),
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.list_lessons(
        user,
        cartridge_id=cartridge_id,
        anomaly_type=anomaly_type,
        item_id=item_id,
    )
