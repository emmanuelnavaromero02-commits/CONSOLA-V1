"""Microsoft Teams channel — inbound/outbound adapters.

Inbound:  Teams Bot Framework activity (dict) → ``InboundTeamsEvent`` →
          ``InternalCopilotRequest``.
Outbound: ``InternalCopilotResponse`` → Teams activity (dict).

The adapter is pure (no I/O, no copilot calls) so it is trivially testable
and can never block the webhook. It defends against missing/oddly-shaped
fields because Teams payloads vary by surface and SDK version.
"""
from __future__ import annotations

import html
import re
from typing import Any

from .schemas import (
    InboundTeamsEvent,
    InternalCopilotRequest,
    InternalCopilotResponse,
    PermissionsContext,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _get(d: Any, *path: str, default: Any = None) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def strip_mentions(text: str, mention_texts: list[str]) -> str:
    """Remove ``<at>Bot</at>`` spans and known mention surface text.

    Teams puts the bot mention inline as HTML (``<at>`` tags) plus an entity
    in ``entities[]``. We strip the tags and any exact mention text so the
    copilot gets the user's actual intent, not "@OmegaBot ...".
    """
    out = text or ""
    # Remove the exact mention surface text FIRST (e.g. "<at>Bot</at>") while
    # the tags are intact, so the bot's name doesn't survive as bare text.
    for m in mention_texts:
        if m:
            out = out.replace(m, " ")
    out = _TAG_RE.sub(" ", out)
    out = html.unescape(out)
    out = _WS_RE.sub(" ", out).replace(" ", " ")
    return out.strip()


def parse_activity(activity: dict[str, Any]) -> InboundTeamsEvent:
    """Collapse a raw Teams activity into transport-level facts."""
    if not isinstance(activity, dict):
        activity = {}

    recipient_id = str(_get(activity, "recipient", "id", default="") or "")
    raw_text = str(activity.get("text") or "")

    # Detect a bot mention via the entities array (stable) and collect the
    # mention surface texts so we can strip them from the user's message.
    mentioned_bot = False
    mention_texts: list[str] = []
    for ent in activity.get("entities") or []:
        if isinstance(ent, dict) and str(ent.get("type", "")).lower() == "mention":
            mentioned = _get(ent, "mentioned", "id", default="")
            mtext = ent.get("text") or ""
            if mtext:
                mention_texts.append(str(mtext))
            if recipient_id and str(mentioned) == recipient_id:
                mentioned_bot = True

    conversation_type = str(_get(activity, "conversation", "conversationType", default="") or "")
    # Personal (1:1) chats often omit conversationType — infer from absence.
    if not conversation_type and not (activity.get("entities")):
        conversation_type = "personal"

    text = strip_mentions(raw_text, mention_texts)

    return InboundTeamsEvent(
        activity_type=str(activity.get("type") or "message"),
        tenant_id=str(_get(activity, "conversation", "tenantId", default="")
                      or _get(activity, "channelData", "tenant", "id", default="") or "") or None,
        aad_object_id=str(_get(activity, "from", "aadObjectId", default="") or "") or None,
        user_id=str(_get(activity, "from", "id", default="") or "") or None,
        user_name=str(_get(activity, "from", "name", default="") or "") or None,
        conversation_id=str(_get(activity, "conversation", "id", default="") or "") or None,
        conversation_type=conversation_type or None,
        message_id=str(activity.get("id") or "") or None,
        text=text,
        service_url=str(activity.get("serviceUrl") or "") or None,
        recipient_id=recipient_id or None,
        mentioned_bot=mentioned_bot,
        raw_attachments=[a for a in (activity.get("attachments") or []) if isinstance(a, dict)],
    )


def _summarise_attachments(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass through only safe, structural attachment metadata — never bytes,
    never SharePoint/Graph URLs with auth. v0.1 surfaces name + contentType
    so the copilot can acknowledge a file without downloading it (download
    needs Graph; see meetings/files scaffolding)."""
    out: list[dict[str, Any]] = []
    for a in raw[:10]:
        out.append({
            "content_type": str(a.get("contentType") or ""),
            "name": str(a.get("name") or ""),
        })
    return out


def to_internal_request(
    event: InboundTeamsEvent,
    ctx: PermissionsContext | dict,
) -> InternalCopilotRequest:
    # Accept a model OR a plain dict and let pydantic build the nested model.
    # Passing a dump (not the instance) also sidesteps class-identity issues
    # when callers import PermissionsContext from a differently-loaded module.
    ctx_data = ctx if isinstance(ctx, dict) else ctx.model_dump()
    return InternalCopilotRequest(
        tenant_id=event.tenant_id,
        user_id=event.aad_object_id or event.user_id,
        conversation_id=event.conversation_id,
        text=event.text,
        attachments=_summarise_attachments(event.raw_attachments),
        metadata={
            "mode": event.mode(),
            "message_id": event.message_id,
            "conversation_type": event.conversation_type,
            "service_url": event.service_url,
        },
        permissions_context=ctx_data,
    )


def from_copilot_response(resp: InternalCopilotResponse) -> dict[str, Any]:
    """Render the internal copilot response as a Teams ``message`` activity.

    Teams markdown is limited; we send text as ``textFormat: markdown``.
    Citations become a compact footer. Adaptive cards / files are future
    (Level 4) and intentionally not fabricated here.
    """
    text = resp.text or ""
    if resp.citations:
        refs = []
        for i, c in enumerate(resp.citations[:5], start=1):
            label = c.get("title") or c.get("dataset") or c.get("source") or f"fuente {i}"
            refs.append(f"[{i}] {label}")
        if refs:
            text = f"{text}\n\n---\n" + "  ·  ".join(refs)
    if not text:
        text = "No tengo una respuesta para eso ahora mismo."
    return {
        "type": "message",
        "textFormat": "markdown",
        "text": text,
    }


def error_activity(message: str = "Algo salió mal procesando tu mensaje. Inténtalo de nuevo.") -> dict[str, Any]:
    """Operator-safe error reply. NEVER carries stacktraces, secrets, or
    internal paths — callers pass a generic message only."""
    return {"type": "message", "textFormat": "markdown", "text": message}
