"""Microsoft Teams channel — HTTP surface.

Two endpoints:
  * POST /api/msteams/messages — the Bot Framework webhook. PUBLIC at the
    console-auth layer (Teams authenticates with its own Bot Framework JWT,
    not a console session); the channel's own security layer gates it. This
    path is added to ``_AUTH_PUBLIC_EXACT`` in main.py.
  * GET  /api/msteams/status   — admin introspection (secret-free). Requires
    a console session + ``settings.read`` permission.

The webhook always answers 200 quickly: Teams retries on non-2xx/slow
responses, which causes duplicate deliveries. Unauthorized/disabled/ignored
requests get an empty 200 (the bot is never advertised to outsiders); a
handled message returns the reply activity in the body.

Delivery note (honest): returning the activity in the HTTP response covers
inline-reply hosts and tests. Production-grade delivery to every Teams
surface uses the Bot Connector "reply to activity" call against the stored
serviceUrl — a documented Level-1 completion item (see channel README).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.channels.msteams import meetings, service
from app.channels.msteams.config import load_config
from app.dependencies import require_authenticated
from app.services.permissions import require_permission

logger = logging.getLogger("msteams.router")

# Fixed webhook path. Keep MSTEAMS_WEBHOOK_PATH aligned with this in the
# Azure Bot endpoint config; the value is surfaced via /status for operators.
WEBHOOK_PATH = "/api/msteams/messages"

router = APIRouter(prefix="/api/msteams", tags=["Microsoft Teams"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/messages")
async def teams_webhook(request: Request) -> Response:
    """Receive a Teams Bot Framework activity."""
    cfg = load_config()
    if cfg.is_level0():
        # Disabled: acknowledge without doing anything (no bot advertised).
        return Response(status_code=200)

    try:
        activity = await request.json()
    except Exception:
        # Malformed body — safe, generic, no echo of the payload.
        return JSONResponse({"status": "bad_request"}, status_code=400)

    if not isinstance(activity, dict):
        return JSONResponse({"status": "bad_request"}, status_code=400)

    result = await service.handle_activity(
        activity,
        auth_header=request.headers.get("authorization"),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        cfg=cfg,
    )

    # Always 200 to avoid Teams retry storms; only emit an activity body when
    # we actually have a reply for the user.
    if result.activity:
        return JSONResponse(result.activity, status_code=200)
    return Response(status_code=200)


@router.get(
    "/status",
    dependencies=[Depends(require_permission("settings.read"))],
)
async def teams_status(user: dict = Depends(require_authenticated)):
    """Secret-free operational status for operators/admins."""
    cfg = load_config()
    return {
        **service.status_snapshot(cfg),
        "meetings": meetings.capabilities_status(cfg),
    }
