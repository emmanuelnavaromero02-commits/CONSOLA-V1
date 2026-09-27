from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.dependencies import require_authenticated
from app.schemas.control_room_direct_actions import (
    DecisionProposalRequest,
    DecisionProposalResponse,
    DirectActionHandleRequest,
    ExceptionApprovalRequest,
    ExceptionApprovalResponse,
    ExceptionReopenRequest,
    ExceptionReopenResponse,
    StudioTargetResponse,
)
from app.services.control_room.authorization_cache import (
    cache_invalidate as _control_room_cache_invalidate,
)
from app.services.control_room.business_decision_proposal import (
    create_decision_proposal,
)
from app.services.control_room.business_exception_approval import (
    approve_exception,
    reopen_exception,
)
from app.services.control_room.business_studio_target import resolve_studio_target
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(tags=["Control Room"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "/actions/exception",
    response_model=ExceptionApprovalResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def control_room_exception_approval(
    request: Request,
    body: ExceptionApprovalRequest,
    user: dict = Depends(require_authenticated),
) -> ExceptionApprovalResponse:
    result = await approve_exception(
        user,
        action_handle=body.action_handle,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _control_room_cache_invalidate(user)
    return result


@router.post(
    "/actions/exception-reopen",
    response_model=ExceptionReopenResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def control_room_exception_reopen(
    request: Request,
    body: ExceptionReopenRequest,
    user: dict = Depends(require_authenticated),
) -> ExceptionReopenResponse:
    result = await reopen_exception(
        user,
        action_handle=body.action_handle,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _control_room_cache_invalidate(user)
    return result


@router.post(
    "/actions/decision-proposal",
    response_model=DecisionProposalResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def control_room_decision_proposal(
    request: Request,
    body: DecisionProposalRequest,
    user: dict = Depends(require_authenticated),
) -> DecisionProposalResponse:
    result = await create_decision_proposal(
        user,
        action_handle=body.action_handle,
        idempotency_key=body.idempotency_key,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _control_room_cache_invalidate(user)
    return result


@router.post(
    "/actions/studio-target",
    response_model=StudioTargetResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def control_room_studio_target(
    body: DirectActionHandleRequest,
    user: dict = Depends(require_authenticated),
) -> StudioTargetResponse:
    return await resolve_studio_target(user, action_handle=body.action_handle)


__all__ = ("router",)
