"""Sprint v1.42 — Copilot endpoints.

Thin HTTP layer over ``copilot_service``. All routes require an
authenticated session; mutating routes also require the CSRF token
header that the wider console already enforces.

Routes:
  POST /api/copilot/conversations                       — create
  GET  /api/copilot/conversations                       — list mine
  GET  /api/copilot/conversations/{cid}                 — read mine
  POST /api/copilot/conversations/{cid}/messages        — send turn
  POST /api/copilot/conversations/{cid}/approve/{mid}   — approve dest.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated
from app.services import copilot_service
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(
    prefix="/api/copilot",
    tags=["copilot"],
    # Service-wide gate: every endpoint needs at least copilot.use.
    # Mutating routes layer on require_csrf below.
    dependencies=[Depends(require_permission("copilot.use"))],
)


def _forensic(request: Request) -> tuple[str | None, str | None]:
    return (
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )


@router.post("/conversations", dependencies=[Depends(require_csrf)])
async def create_conversation(
    body: dict,
    user: dict = Depends(require_authenticated),
):
    # Sprint v1.42 R1 security finding: never trust ``workspace_id`` from
    # the body — that would let a caller attach the conversation to a
    # workspace they don't belong to. The active workspace comes from
    # the authenticated session (already vetted by the auth layer).
    title = (body or {}).get("title")
    workspace_id = user.get("active_workspace_id")
    return await copilot_service.create_conversation(
        user_id=user["id"], workspace_id=workspace_id, title=title,
    )


@router.get("/conversations")
async def list_conversations(user: dict = Depends(require_authenticated)):
    return await copilot_service.list_conversations(
        user_id=user["id"],
        workspace_id=user.get("active_workspace_id"),
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: dict = Depends(require_authenticated),
):
    return await copilot_service.get_conversation_messages(
        conversation_id=conversation_id, user=user,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    dependencies=[Depends(require_csrf)],
)
async def send_message(
    conversation_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    message = ((body or {}).get("message") or "").strip()
    if not message:
        raise HTTPException(400, "empty message")
    ip, ua = _forensic(request)
    return await copilot_service.run_turn(
        conversation_id=conversation_id,
        user_message=message,
        user=user,
        ip=ip, user_agent=ua,
    )


@router.post(
    "/conversations/{conversation_id}/approve/{message_id}",
    dependencies=[Depends(require_csrf)],
)
async def approve_action(
    conversation_id: str,
    message_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    ip, ua = _forensic(request)
    return await copilot_service.approve_pending_action(
        conversation_id=conversation_id,
        message_id=message_id,
        user=user,
        ip=ip, user_agent=ua,
    )
