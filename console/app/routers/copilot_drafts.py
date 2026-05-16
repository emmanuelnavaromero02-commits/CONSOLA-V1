"""Sprint v1.44.2 (Tarea H) — copilot drafts (intelligent writing).

Surface for the upcoming "Redact a follow-up email" feature. This
session ships the durable CRUD layer; the LLM generation path is
the next-session integration in copilot_service.

Lifecycle: draft → sent | discarded. Sending happens through a
separate approved channel (next session); /send below stamps the
draft as ``sent`` and records the audit event but doesn't (yet)
deliver email/Slack/etc.
"""
from __future__ import annotations

import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated
from app.services import audit_service, auth
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


def _validate_uuid(value: str, *, label: str) -> str:
    """v1.44.2 (R1 Security P2): early UUID validation so a malformed
    path param surfaces as a clean 400 instead of a 500 from
    asyncpg's InvalidTextRepresentation."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, f"Invalid {label}")


router = APIRouter(
    prefix="/api/copilot/drafts",
    tags=["copilot-drafts"],
    dependencies=[Depends(require_permission("copilot.use"))],
)


_ALLOWED_KINDS = {"email", "memo", "note", "report"}
_ALLOWED_TONES = {"formal", "neutral", "friendly", "urgent"}
_MAX_BODY_LEN = 50_000


def _serialize(row: dict) -> dict:
    """Render an asyncpg Record-as-dict into JSON-friendly shape."""
    out = dict(row)
    out["id"] = str(out["id"])
    if "metadata" in out and isinstance(out["metadata"], str):
        try:
            out["metadata"] = json.loads(out["metadata"])
        except Exception:
            out["metadata"] = {}
    return out


@router.post("", dependencies=[Depends(require_csrf)])
async def create_draft(
    body: dict,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Persist a new draft.

    Body: ``{kind, body, title?, tone?, metadata?}``. The LLM
    generation path (next session) calls this same endpoint with
    its produced text; for now the frontend can also call it
    directly to round-trip a user-edited draft.

    Note: this endpoint does NOT call the LLM. The brief explicitly
    flags LLM integration as next-session work. A future
    POST /generate endpoint will wrap an LLM call + persist its
    output via this same shape.
    """
    payload = body or {}
    kind  = payload.get("kind",  "")
    text  = payload.get("body",  "")
    title = payload.get("title", "")
    tone  = payload.get("tone",  "neutral")
    metadata = payload.get("metadata", {}) or {}

    if kind not in _ALLOWED_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(_ALLOWED_KINDS)}")
    if tone not in _ALLOWED_TONES:
        raise HTTPException(400, f"tone must be one of {sorted(_ALLOWED_TONES)}")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "body is required")
    if len(text) > _MAX_BODY_LEN:
        raise HTTPException(
            400, f"body too long (max {_MAX_BODY_LEN} chars)"
        )
    if not isinstance(metadata, dict):
        raise HTTPException(400, "metadata must be an object")

    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO copilot_drafts (user_id, kind, title, body, tone, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id, user_id, kind, title, body, tone, status, metadata,
                  created_at, updated_at
        """,
        user["id"], kind, title, text, tone, json.dumps(metadata),
    )
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.draft.create",
        resource_type="copilot_draft",
        resource_id=str(row["id"]),
        status="success",
        metadata={"kind": kind, "tone": tone, "body_len": len(text)},
    )
    return {"ok": True, "draft": _serialize(row)}


@router.get("")
async def list_drafts(
    status: str | None = None,
    user: dict = Depends(require_authenticated),
):
    """List the current user's drafts, optionally filtered by
    status (draft | sent | discarded)."""
    if status is not None and status not in {"draft", "sent", "discarded"}:
        raise HTTPException(400, "Invalid status filter")
    pool = await auth.pool()
    if status is None:
        rows = await pool.fetch(
            """
            SELECT id, user_id, kind, title, body, tone, status, metadata,
                   created_at, updated_at
              FROM copilot_drafts
             WHERE user_id = $1
             ORDER BY updated_at DESC
             LIMIT 200
            """,
            user["id"],
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, user_id, kind, title, body, tone, status, metadata,
                   created_at, updated_at
              FROM copilot_drafts
             WHERE user_id = $1 AND status = $2
             ORDER BY updated_at DESC
             LIMIT 200
            """,
            user["id"], status,
        )
    return {"drafts": [_serialize(r) for r in rows]}


@router.post("/{draft_id}/send", dependencies=[Depends(require_csrf)])
async def send_draft(
    draft_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    """Mark a draft as ``sent``.

    v1.44.2 caveat: this endpoint stamps the status but does NOT
    actually deliver the message — channel integration (SMTP,
    Slack, etc.) is the next-session task. The audit event flags
    ``delivery_pending=true`` so an analytics query can spot drafts
    in the "sent but not delivered" interim state.
    """
    draft_id = _validate_uuid(draft_id, label="draft_id")
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        UPDATE copilot_drafts
           SET status = 'sent', updated_at = NOW()
         WHERE id = $1 AND user_id = $2 AND status = 'draft'
        RETURNING id, status
        """,
        draft_id, user["id"],
    )
    if row is None:
        # 404 if the draft doesn't exist OR isn't this user's OR is
        # already sent / discarded. We don't distinguish between these
        # so a probing client can't enumerate other users' draft IDs.
        raise HTTPException(404, "Draft not found")
    await audit_service.record_event(
        user_id=user["id"],
        email=user.get("email"),
        action="copilot.draft.send",
        resource_type="copilot_draft",
        resource_id=str(draft_id),
        status="success",
        metadata={"delivery_pending": True},
    )
    return {"ok": True, "draft_id": draft_id, "status": "sent"}
