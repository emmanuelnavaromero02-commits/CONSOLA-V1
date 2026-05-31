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
    # ``str.replace`` raises TypeError on non-string arguments; the public
    # function must never raise on hostile input, so coerce defensively.
    for m in mention_texts:
        if m and isinstance(m, str):
            out = out.replace(m, " ")
    out = _TAG_RE.sub(" ", out)
    out = html.unescape(out)
    out = _WS_RE.sub(" ", out).replace(" ", " ")
    return out.strip()


def adaptive_card_submit_value(activity: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``value`` payload when a Teams message is an Adaptive Card
    ``Action.Submit`` (button press), else None.

    Teams delivers card-button presses as ``type=message`` activities with an
    empty ``text`` and a populated ``value`` object. We MUST detect them
    before the empty-text gate fires, and route them to the approval handler
    instead of the copilot. A non-dict value is treated as "not a submit"
    (defensive: hostile clients can put anything there).
    """
    if not isinstance(activity, dict):
        return None
    val = activity.get("value")
    if isinstance(val, dict) and val.get("intent"):
        return val
    return None


def parse_activity(activity: dict[str, Any]) -> InboundTeamsEvent:
    """Collapse a raw Teams activity into transport-level facts."""
    if not isinstance(activity, dict):
        activity = {}

    recipient_id = str(_get(activity, "recipient", "id", default="") or "")
    raw_text = str(activity.get("text") or "")

    # Detect a bot mention via the entities array (stable) and collect the
    # mention surface texts so we can strip them from the user's message.
    # Hostile-input safe: a non-list ``entities`` (int, dict, string) MUST
    # NOT make the iteration raise — Teams payload shapes vary across SDKs.
    mentioned_bot = False
    mention_texts: list[str] = []
    raw_entities = activity.get("entities")
    if not isinstance(raw_entities, list):
        raw_entities = []
    for ent in raw_entities:
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
    needs Graph; see meetings/files scaffolding).

    Hostile-input safe: non-dict entries are skipped silently. ``parse_activity``
    already filters, but this helper must defend itself in case a future
    caller passes raw activity payloads directly.
    """
    out: list[dict[str, Any]] = []
    for a in raw[:10]:
        if not isinstance(a, dict):
            continue
        out.append({
            "content_type": str(a.get("contentType") or ""),
            "name": str(a.get("name") or ""),
        })
    return out


def to_internal_request(
    event: InboundTeamsEvent,
    ctx: PermissionsContext | dict,
) -> InternalCopilotRequest:
    """Build the internal copilot request from a Teams event.

    Contract notes (round-3 audit):
      * ``permissions_context`` is **audit / provenance only**. ``run_turn``
        does not read it; the copilot recomputes authorization from
        ``user.role`` via ``permissions.has_permission``. We still pass it so
        a future copilot, the audit log, or downstream tooling can reason
        about how Teams resolved the caller.
      * ``attachments`` carry only safe structural metadata (name +
        content_type, capped at 10). The current ``run_turn`` ignores them
        — surfacing file context to the copilot is a documented Level-4
        item. Until then a Teams user sending a file with no text body
        looks empty to the copilot.
    """
    # Accept a model OR a plain dict and let pydantic build the nested model.
    # Passing a dump (not the instance) also sidesteps class-identity issues
    # when callers import PermissionsContext from a differently-loaded module.
    # If neither: fall back to an empty context rather than raise — Teams
    # transport must never 500 the webhook.
    if isinstance(ctx, dict):
        ctx_data: dict = ctx
    elif hasattr(ctx, "model_dump"):
        ctx_data = ctx.model_dump()
    else:
        ctx_data = {}
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
            # Display name surfaced to the copilot as UX context (e.g. for a
            # natural "Hola, Juan" reply). Auth is unchanged — the copilot
            # still derives permissions from the resolved console user.role,
            # not from this string. Bots that trust the name as identity are
            # a known anti-pattern.
            "teams_user_name": event.user_name,
        },
        permissions_context=ctx_data,
    )


_APPROVAL_INTENT_APPROVE = "msteams.approval.approve"
_APPROVAL_INTENT_REJECT = "msteams.approval.reject"
_ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"


def _approval_card(
    *, conversation_id: str, message_id: str, pending: list[dict[str, Any]],
    text: str,
) -> dict[str, Any]:
    """Build the Adaptive Card the bot posts when the copilot needs approval.

    The card embeds the SAME identifiers the console UI uses to drive
    ``copilot_service.approve_pending_action`` (conversation_id + message_id),
    not a parallel approval namespace. Button payloads are signed implicitly
    by Bot Framework JWT on the inbound invoke; the service still verifies
    the AAD allowlist and the per-conversation ownership inside the copilot
    (so a crafted payload cannot approve someone else's pending action).

    We render only safe, structural fields (tool name + risk_level + a short
    server hint). We DO NOT echo raw ``args`` — they can carry sensitive data
    (column values, dataset rows). The operator approves by name, never by
    raw payload preview.
    """
    facts: list[dict[str, str]] = []
    for action in (pending or [])[:5]:
        if not isinstance(action, dict):
            continue
        name = str(action.get("bare_name") or action.get("tool") or "tool")
        risk = str(action.get("risk_level") or "")
        server = str(action.get("server") or "")
        value = name if not server else f"{name}  ·  {server}"
        if risk:
            value = f"{value}  ·  riesgo: {risk}"
        facts.append({"title": "Acción:", "value": value[:200]})
    if not facts:
        facts.append({"title": "Acción:", "value": "(detalle no disponible)"})

    body_text = (text or "").strip() or "El copiloto pide aprobación humana para continuar."

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Aprobación requerida", "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": body_text, "wrap": True},
            {"type": "FactSet", "facts": facts},
            {
                "type": "TextBlock",
                "text": (
                    "Tu identidad de consola se valida en el servidor antes de "
                    "ejecutar; rechazar audita la decisión pero no borra la "
                    "acción pendiente (puedes resolverla también desde la consola)."
                ),
                "wrap": True, "isSubtle": True, "size": "Small",
            },
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Aprobar",
                "style": "positive",
                "data": {
                    "intent": _APPROVAL_INTENT_APPROVE,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                },
            },
            {
                "type": "Action.Submit",
                "title": "Rechazar",
                "style": "destructive",
                "data": {
                    "intent": _APPROVAL_INTENT_REJECT,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                },
            },
        ],
    }
    return {
        "type": "message",
        "attachments": [{
            "contentType": _ADAPTIVE_CARD_CONTENT_TYPE,
            "content": card,
        }],
    }


def approval_outcome_card(*, status: str, summary: str | None = None) -> dict[str, Any]:
    """Render an outcome card (approved / rejected / failed) that replaces
    the approval prompt UX-wise. Plain text — no buttons — so a second
    accidental tap cannot replay the action."""
    if status == "approved":
        head, color = "Acción aprobada", "good"
    elif status == "rejected":
        head, color = "Acción rechazada", "attention"
    else:
        head, color = "No se pudo procesar la aprobación", "warning"
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": head, "weight": "Bolder", "size": "Medium", "color": color},
    ]
    if summary:
        body.append({"type": "TextBlock", "text": summary, "wrap": True, "isSubtle": True})
    return {
        "type": "message",
        "attachments": [{
            "contentType": _ADAPTIVE_CARD_CONTENT_TYPE,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": body,
            },
        }],
    }


def from_copilot_response(
    resp: InternalCopilotResponse,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Render the internal copilot response as a Teams ``message`` activity.

    Teams markdown is limited; we send text as ``textFormat: markdown``.
    Citations become a compact footer. When the copilot stages a pending
    action (``requires_approval``), the response becomes an Adaptive Card
    with Approve / Reject buttons instead of a plain text reply — see
    ``_approval_card``.
    """
    # Approval path: render the card if we have everything needed to route
    # the button press back to copilot_service.approve_pending_action. Falls
    # back to plain text when ids are missing rather than rendering a
    # button-less card the user couldn't act on.
    if resp.requires_approval and resp.requires_approval_message_id and conversation_id:
        return _approval_card(
            conversation_id=conversation_id,
            message_id=resp.requires_approval_message_id,
            pending=resp.pending_actions or [],
            text=resp.text or "",
        )

    text = resp.text or ""
    # Citations come from the copilot (DB / LLM extraction) and may be
    # hostile-shaped in edge cases (None entries, non-dict items). This
    # function runs OUTSIDE the service.handle_activity copilot try/except,
    # so a single bad citation would 500 the webhook → Teams retry storm.
    # Best-effort: skip non-dict entries silently, never raise.
    if resp.citations:
        refs = []
        for i, c in enumerate(resp.citations[:5], start=1):
            if not isinstance(c, dict):
                continue
            # Copilot citations carry ``entity`` (dataset/table) and ``tool``
            # (mcp tool that produced them) plus a ``source`` server id. Prefer
            # human-readable names; fall back to source (server id) and finally
            # an ordinal label so the citation footer is never empty.
            label = (
                c.get("title")
                or c.get("dataset")
                or c.get("entity")
                or c.get("tool")
                or c.get("source")
                or f"fuente {i}"
            )
            # Cap per-label length so a hostile/garbage citation title can't
            # bloat the reply past Teams' message-size budget (~4 KB for the
            # plain-text body). 200 chars is well above realistic dataset /
            # tool / entity names.
            label = str(label)[:200]
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
