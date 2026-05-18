"""SMTP delivery for copilot drafts.

The draft router owns CRUD and generation. This service owns the
delivery boundary: validate ownership, send through the existing SMTP
helper, persist delivery state, and audit without storing full message
body in metadata.
"""
from __future__ import annotations

import html
import inspect
import json
import os
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth, email_service


def _is_admin(user: dict[str, Any]) -> bool:
    return str(user.get("role", "")).lower() in {"admin", "owner", "super_admin"}


def _extract_recipient(metadata: dict[str, Any]) -> str:
    for key in ("to", "recipient", "recipient_email", "email"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _subject_for_draft(row: dict[str, Any], metadata: dict[str, Any]) -> str:
    value = metadata.get("subject")
    if isinstance(value, str) and value.strip():
        return value.strip()
    title = row.get("title")
    return title.strip() if isinstance(title, str) else ""


def _html_for_body(body: str) -> str:
    escaped = html.escape(body).replace("\n", "<br>")
    return f"<p>{escaped}</p>"


def _production_missing_smtp_host() -> bool:
    return (
        os.environ.get("APP_ENV", "").lower() in {"production", "prod"}
        and not os.environ.get("SMTP_HOST")
    )


async def _send_email(*, to: str, subject: str, body: str, from_user: str | None) -> bool:
    """Call the existing email helper, passing from_user only if the
    helper grows that parameter in a later sprint."""
    kwargs: dict[str, Any] = {
        "to": to,
        "subject": subject,
        "html": _html_for_body(body),
        "text": body,
    }
    if "from_user" in inspect.signature(email_service.send_email).parameters:
        kwargs["from_user"] = from_user
    return await email_service.send_email(**kwargs)


async def _mark_failed(pool: Any, draft_id: str, error: str) -> None:
    await pool.execute(
        """
        UPDATE copilot_drafts
           SET status = 'failed',
               updated_at = NOW(),
               delivery_log = $2::jsonb
         WHERE id = $1
        """,
        draft_id,
        json.dumps({"ok": False, "error": error}),
    )


async def send_draft(draft_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """Deliver a draft via SMTP and persist its delivery result.

    ``metadata.to`` (or one of the backward-compatible aliases) is the
    recipient. ``metadata.subject`` wins over title; body is the email
    text. SMTP failures never mark a draft as sent.
    """
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_id, kind, title, body, tone, status, metadata
          FROM copilot_drafts
         WHERE id = $1
        """,
        draft_id,
    )
    if row is None:
        raise HTTPException(404, "Draft not found")

    draft = dict(row)
    owner_id = draft.get("user_id")
    if owner_id != user.get("id") and not _is_admin(user):
        raise HTTPException(404, "Draft not found")
    if draft.get("status") != "draft":
        raise HTTPException(404, "Draft not found")

    metadata = draft.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    recipient = _extract_recipient(metadata)
    subject = _subject_for_draft(draft, metadata)
    body = draft.get("body") or ""
    if not recipient:
        raise HTTPException(400, "draft recipient is required")
    if not subject:
        raise HTTPException(400, "draft subject is required")
    if not isinstance(body, str) or not body.strip():
        raise HTTPException(400, "draft body is required")

    if _production_missing_smtp_host():
        error = "SMTP_HOST is required in production"
        await _mark_failed(pool, draft_id, error)
        raise HTTPException(500, error)

    sent = await _send_email(
        to=recipient,
        subject=subject,
        body=body,
        from_user=user.get("email"),
    )
    if not sent:
        error = "smtp_delivery_failed"
        await _mark_failed(pool, draft_id, error)
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="draft_sent",
            resource_type="copilot_draft",
            resource_id=draft_id,
            status="failure",
            metadata={"to": recipient, "subject": subject, "error": error},
        )
        return {"ok": False, "draft_id": draft_id, "status": "failed", "error": error}

    delivery_log = {
        "ok": True,
        "channel": "smtp",
        "to": recipient,
        "subject": subject,
        "from_user": user.get("email"),
    }
    await pool.execute(
        """
        UPDATE copilot_drafts
           SET status = 'sent',
               sent_at = NOW(),
               updated_at = NOW(),
               delivery_log = $2::jsonb
         WHERE id = $1
        """,
        draft_id,
        json.dumps(delivery_log),
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="draft_sent",
        resource_type="copilot_draft",
        resource_id=draft_id,
        status="success",
        metadata={"to": recipient, "subject": subject},
    )
    return {
        "ok": True,
        "draft_id": draft_id,
        "status": "sent",
        "delivery": {"channel": "smtp", "to": recipient, "subject": subject},
    }
