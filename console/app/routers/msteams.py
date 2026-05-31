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

import asyncio
import json
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

# Hard cap on the inbound activity body. Real Bot Framework payloads are
# tens of KB; 1 MB is comfortably above that and keeps an attacker from
# allocating multi-GB before parsing.
_MAX_BODY_BYTES = 1 * 1024 * 1024

# Hard timeout for receiving the request body. Defeats slowloris-style attacks
# (chunked transfer with drip-fed bytes) that bypass the Content-Length cap
# because the header is absent. 15 s is well above realistic Bot Framework
# latency (sub-second) but tight enough to free the worker quickly under
# attack. Without this cap, a single hostile connection can pin a worker
# until socket timeout (potentially minutes).
_BODY_READ_TIMEOUT = 15.0

router = APIRouter(prefix="/api/msteams", tags=["Microsoft Teams"])


def _client_ip(request: Request) -> str | None:
    # Delegate to the platform-wide proxy-aware helper so the Teams webhook
    # sees the same client IP that the rate-limit middleware uses. Defining
    # a local request.client.host shortcut would key audit + downstream
    # services on the REVERSE-PROXY IP instead of the real sender — a real
    # divergence found in round-2 audit. Import inline to avoid a circular
    # import at module load (main imports the router).
    from app.main import _client_ip as _resolve
    try:
        return _resolve(request)
    except Exception:
        return request.client.host if request.client else None


@router.post("/messages")
async def teams_webhook(request: Request) -> Response:
    """Receive a Teams Bot Framework activity."""
    cfg = load_config()
    if cfg.is_level0():
        # Disabled: acknowledge without doing anything (no bot advertised).
        # Body is NOT consumed here — keeps the disabled fast-path immune
        # from slowloris / oversized-body attacks.
        return Response(status_code=200)

    # Production guard: refuse jwt_validation=disabled when APP_ENV is
    # production. Disabled mode is documented as local-dev / trusted-gateway
    # only; if it ever leaks into production config, fail closed loudly.
    import os as _os
    if cfg.jwt_mode == "disabled" and _os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}:
        return Response(status_code=503)

    # Body-size cap: a Bot Framework activity is small (typically < 32 KB).
    # Reject anything over 1 MB to keep an attacker from filling memory with
    # multi-GB POSTs before parsing fails.
    declared_len = request.headers.get("content-length")
    if declared_len and declared_len.isdigit() and int(declared_len) > _MAX_BODY_BYTES:
        return JSONResponse({"status": "bad_request"}, status_code=413)

    # Read the body with a hard timeout + post-read size check. Content-Length
    # can be omitted (chunked encoding) — without a timeout an attacker can
    # drip-feed bytes and pin a worker until the OS socket gives up. The size
    # check after reading catches over-cap bodies that hid behind a missing
    # Content-Length.
    try:
        raw = await asyncio.wait_for(request.body(), timeout=_BODY_READ_TIMEOUT)
    except asyncio.TimeoutError:
        return JSONResponse({"status": "bad_request"}, status_code=408)
    except Exception:
        return JSONResponse({"status": "bad_request"}, status_code=400)

    if len(raw) > _MAX_BODY_BYTES:
        return JSONResponse({"status": "bad_request"}, status_code=413)

    try:
        activity = json.loads(raw or b"{}")
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
