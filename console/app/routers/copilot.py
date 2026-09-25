from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.dependencies import require_authenticated
from app.services import (
    audit_service,
    copilot_context_service,
    copilot_service,
    proactive_service,
)
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(
    prefix="/api/copilot",
    tags=["copilot"],
    dependencies=[Depends(require_permission("copilot.use"))],
)


def _forensic(request: Request) -> tuple[str | None, str | None]:
    return (
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )


def _sse(event: str, data: dict | None = None) -> str:
    if data is None:
        return f"event: {event}\ndata: {{}}\n\n"
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }


def _event_to_sse(evt: dict) -> str:
    typ = str(evt.get("type") or "message")
    if typ == "heartbeat":
        return ": keep-alive\n\n"
    if typ == "ready":
        return _sse(
            "ready", {"ok": True, "conversation_id": evt.get("conversation_id")}
        )
    if typ == "text_delta":
        return _sse("token", {"delta": evt.get("text") or ""})
    if typ == "text":
        return _sse("message", {"text": evt.get("text") or ""})
    if typ == "done":
        result = evt.get("result")
        return _sse("done", result if isinstance(result, dict) else {})
    if typ == "error":
        return _sse(
            "error",
            {
                "status_code": evt.get("status_code") or 500,
                "detail": evt.get("detail") or "stream error",
            },
        )
    return _sse(typ, {k: v for k, v in evt.items() if k != "type"})


@router.post("/conversations", dependencies=[Depends(require_csrf)])
async def create_conversation(
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    title = (body or {}).get("title")
    result = await copilot_service.create_conversation(
        user=user,
        title=title,
    )
    ip, ua = _forensic(request)
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.conversation.create",
        resource_type="conversation",
        resource_id=result.get("id"),
        ip=ip,
        user_agent=ua,
        status="success",
        metadata={"title_len": len(title or "")},
    )
    return result


@router.get("/conversations")
async def list_conversations(user: dict = Depends(require_authenticated)):
    return await copilot_service.list_conversations(
        user=user,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: dict = Depends(require_authenticated),
):
    return await copilot_service.get_conversation_messages(
        conversation_id=conversation_id,
        user=user,
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
    result = await copilot_service.run_turn(
        conversation_id=conversation_id,
        user_message=message,
        user=user,
        ip=ip,
        user_agent=ua,
    )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.message.send",
        resource_type="conversation",
        resource_id=conversation_id,
        ip=ip,
        user_agent=ua,
        status="success",
        metadata={"message_len": len(message)},
        conversation_id=conversation_id,
    )
    return result


@router.get("/chat/{conversation_id}/stream")
async def chat_stream_probe(
    conversation_id: str,
    user: dict = Depends(require_authenticated),
):
    """SSE readiness probe for clients before opening a mutating stream.

    This GET route is intentionally side-effect-free. The POST route below
    performs the real user-message turn and enforces CSRF.
    """
    try:
        uuid.UUID(str(conversation_id))
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid conversation_id: not a UUID")

    async def event_source():
        yield _sse("ready", {"ok": True, "conversation_id": conversation_id})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


@router.post(
    "/chat/{conversation_id}/stream",
    dependencies=[Depends(require_csrf)],
)
async def stream_message(
    conversation_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    message = ((body or {}).get("message") or "").strip()
    if not message:
        raise HTTPException(400, "empty message")
    ip, ua = _forensic(request)
    events = await copilot_service.open_turn_stream(
        conversation_id=conversation_id,
        user_message=message,
        user=user,
        ip=ip,
        user_agent=ua,
    )

    async def event_source():
        audited = False
        async for evt in events:
            if evt.get("type") == "done" and not audited:
                audited = True
                await audit_service.record_event(
                    user_id=user["id"],
                    email=user.get("email"),
                    action="copilot.message.send",
                    resource_type="conversation",
                    resource_id=conversation_id,
                    ip=ip,
                    user_agent=ua,
                    status="success",
                    metadata={"message_len": len(message), "stream": True},
                    conversation_id=conversation_id,
                )
            elif evt.get("type") == "error" and not audited:
                audited = True
                await audit_service.record_event(
                    user_id=user["id"],
                    email=user.get("email"),
                    action="copilot.message.send",
                    resource_type="conversation",
                    resource_id=conversation_id,
                    ip=ip,
                    user_agent=ua,
                    status="error",
                    metadata={
                        "message_len": len(message),
                        "stream": True,
                        "error": str(evt.get("detail") or "stream error")[:200],
                    },
                    conversation_id=conversation_id,
                )
            yield _event_to_sse(evt)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=_stream_headers(),
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
    result = await copilot_service.approve_pending_action(
        conversation_id=conversation_id,
        message_id=message_id,
        user=user,
        ip=ip,
        user_agent=ua,
    )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.action.approve",
        resource_type="conversation_message",
        resource_id=message_id,
        ip=ip,
        user_agent=ua,
        status="success",
        metadata={"conversation_id": conversation_id},
        conversation_id=conversation_id,
    )
    return result


@router.get("/briefing")
async def get_briefing(
    user: dict = Depends(require_authenticated),
):
    """Return the user-scoped briefing.

    The 4 analyzers in proactive_service produce up to a few dozen
    candidate highlights; this endpoint aggregates them, drops the
    user's dismissed entries, sorts by severity, and returns at most
    6 (the brief's documented cap). Frontend renders one card per
    highlight on the dashboard.
    """
    highlights = await proactive_service.briefing_for_user(
        user["id"], user_context=user
    )
    try:
        live = copilot_context_service.project_operator_recommendations(
            await copilot_context_service.list_recommendations(user, limit=6)
        )
        live_highlights = [
            {
                "id": item.get("id"),
                "severity": item.get("severity") or "info",
                "title": item.get("title") or "Recomendación",
                "body": item.get("body") or "",
                "category": item.get("category") or "console",
                "action_label": item.get("action_label"),
                "action_href": item.get("action_href"),
            }
            for item in live.get("recommendations", [])
            if isinstance(item, dict) and item.get("status") != "dismissed"
        ]
        seen: set[str] = set()
        merged = []
        for item in [*live_highlights, *highlights]:
            key = str(item.get("id") or item.get("title") or "")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        highlights = merged[:6]
    except Exception:
        pass
    return {"highlights": highlights}


@router.get(
    "/context/snapshot",
    dependencies=[Depends(require_permission("operations.read"))],
)
async def get_live_context_snapshot(
    user: dict = Depends(require_authenticated),
):
    """Latest persisted workspace-wide console cut for Copilot."""
    raw = await copilot_context_service.latest_snapshot(user)
    return copilot_context_service.project_operator_snapshot(raw)


@router.post(
    "/context/refresh",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("operations.read")),
        Depends(require_permission("control_room.write")),
    ],
)
async def refresh_live_context(
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Refresh the live console context now for the active workspace."""
    ip, ua = _forensic(request)
    result = await copilot_context_service.collect_workspace_context(
        user,
        generated_by="manual",
        persist=True,
        ip=ip,
        user_agent=ua,
    )
    return copilot_context_service.project_operator_snapshot(result)


@router.get("/recommendations")
async def list_live_recommendations(
    limit: int = 20,
    include_dismissed: bool = False,
    user: dict = Depends(require_authenticated),
):
    raw = await copilot_context_service.list_recommendations(
        user, limit=limit, include_dismissed=include_dismissed
    )
    return copilot_context_service.project_operator_recommendations(raw)


@router.post(
    "/recommendations/{recommendation_id}/dismiss",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def dismiss_live_recommendation(
    recommendation_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    ip, ua = _forensic(request)
    result = await copilot_context_service.dismiss_recommendation(
        user,
        recommendation_id,
        ip=ip,
        user_agent=ua,
    )
    return copilot_context_service.project_dismissed_recommendation(result)


@router.post(
    "/briefing/{highlight_id}/dismiss",
    dependencies=[Depends(require_csrf)],
)
async def dismiss_briefing(
    highlight_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Record a per-user dismissal. Idempotent — calling twice flips
    the bit once and audits two events so we can spot suspicious
    repeated dismissals.

    The highlight_id is forensic: it has the shape
    ``<category>:<cartridge_or_job>`` (e.g. ``freshness:sap_hcm``).
    Reject empty IDs at the boundary so a malformed POST can't
    insert a row that mass-dismisses everything later.
    """
    highlight_id = (highlight_id or "").strip()
    if not highlight_id or len(highlight_id) > 200:
        raise HTTPException(400, "Invalid highlight_id")
    await proactive_service.dismiss_highlight(user["id"], highlight_id)
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.briefing.dismiss",
        resource_type="briefing_highlight",
        resource_id=highlight_id,
        status="success",
    )
    return {"ok": True, "dismissed": highlight_id}
