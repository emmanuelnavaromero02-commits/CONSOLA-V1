from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import require_authenticated
from app.services import external_actions
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/actions", tags=["External Actions"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionProposalRequest(_StrictModel):
    source_type: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=256)
    action_type: str = Field(min_length=1, max_length=128)
    adapter_name: Literal["sandbox"] = "sandbox"
    payload: dict[str, Any] = Field(default_factory=dict)
    dry_run_payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=160)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=604800)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionMutationRequest(_StrictModel):
    idempotency_key: str | None = Field(default=None, max_length=160)


class ActionDryRunRequest(ActionMutationRequest):
    dry_run_payload: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/propose",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def propose_action(
    body: ActionProposalRequest,
    user: dict = Depends(require_authenticated),
):
    return await external_actions.propose(user, body.model_dump())


@router.get("", dependencies=[Depends(require_permission("control_room.write"))])
async def list_actions(
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await external_actions.list_actions(user, limit=limit)


@router.get("/{action_id}", dependencies=[Depends(require_permission("control_room.write"))])
async def get_action(action_id: str, user: dict = Depends(require_authenticated)):
    return await external_actions.get_action(user, action_id)


@router.post(
    "/{action_id}/dry-run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def dry_run_action(
    action_id: str,
    body: ActionDryRunRequest = Body(default_factory=ActionDryRunRequest),
    user: dict = Depends(require_authenticated),
):
    return await external_actions.dry_run(user, action_id, body.model_dump())


@router.post(
    "/{action_id}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.execute"))],
)
async def approve_action(
    action_id: str,
    body: ActionMutationRequest = Body(default_factory=ActionMutationRequest),
    user: dict = Depends(require_authenticated),
):
    return await external_actions.approve(user, action_id, body.model_dump())


@router.post(
    "/{action_id}/reject",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def reject_action(
    action_id: str,
    body: ActionMutationRequest = Body(default_factory=ActionMutationRequest),
    user: dict = Depends(require_authenticated),
):
    return await external_actions.reject(user, action_id, body.model_dump())


@router.post(
    "/{action_id}/execute",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.execute"))],
)
async def execute_action(
    request: Request,
    action_id: str,
    body: ActionMutationRequest = Body(default_factory=ActionMutationRequest),
    user: dict = Depends(require_authenticated),
):
    _ = request
    return await external_actions.execute(user, action_id, body.model_dump())


@router.post(
    "/{action_id}/cancel",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def cancel_action(
    action_id: str,
    body: ActionMutationRequest = Body(default_factory=ActionMutationRequest),
    user: dict = Depends(require_authenticated),
):
    return await external_actions.cancel(user, action_id, body.model_dump())
