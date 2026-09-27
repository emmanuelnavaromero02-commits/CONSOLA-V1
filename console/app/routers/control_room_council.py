from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request

from app.dependencies import require_authenticated
from app.schemas.control_room_council import (
    ActionCouncilResponse,
    CouncilApproveRequest,
    CouncilApproveResponse,
    CouncilDiscardRequest,
    CouncilDiscardResponse,
    CouncilRenewRequest,
    CouncilRenewResponse,
)
from app.services.control_room.authorization_cache import (
    cache_invalidate as _control_room_cache_invalidate,
)
from app.services.control_room.business_council_commands import (
    approve_council_proposal,
    discard_council_proposal,
    renew_council_proposal,
)
from app.services.control_room.business_council_view import build_action_council
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(tags=["Control Room"])
ProposalId = Path(pattern=r"^[a-f0-9]{64}$")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get(
    "/council",
    response_model=ActionCouncilResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_council(
    user: dict = Depends(require_authenticated),
) -> ActionCouncilResponse:
    return await build_action_council(user)


@router.post(
    "/council/{proposal_id}/approve",
    response_model=CouncilApproveResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.approve")),
    ],
)
async def control_room_council_approve(
    request: Request,
    body: CouncilApproveRequest,
    proposal_id: str = ProposalId,
    user: dict = Depends(require_authenticated),
) -> CouncilApproveResponse:
    result = await approve_council_proposal(
        user,
        proposal_id,
        idempotency_key=body.idempotency_key,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _control_room_cache_invalidate(user)
    return result


@router.post(
    "/council/{proposal_id}/discard",
    response_model=CouncilDiscardResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.approve")),
    ],
)
async def control_room_council_discard(
    request: Request,
    body: CouncilDiscardRequest,
    proposal_id: str = ProposalId,
    user: dict = Depends(require_authenticated),
) -> CouncilDiscardResponse:
    result = await discard_council_proposal(
        user,
        proposal_id,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _control_room_cache_invalidate(user)
    return result


@router.post(
    "/council/{proposal_id}/renew",
    response_model=CouncilRenewResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def control_room_council_renew(
    request: Request,
    body: CouncilRenewRequest,
    proposal_id: str = ProposalId,
    user: dict = Depends(require_authenticated),
) -> CouncilRenewResponse:
    del body
    result = await renew_council_proposal(
        user,
        proposal_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _control_room_cache_invalidate(user)
    return result


__all__ = ("router",)
