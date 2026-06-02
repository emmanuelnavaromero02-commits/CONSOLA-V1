"""Microsoft Teams channel — orchestration.

This is the ONLY place the channel touches the rest of the platform. It
wires: security gates → adapter → console-user resolution → conversation
continuity → the EXISTING copilot (``copilot_service.run_turn``) → outbound
adapter → audit. No business logic lives here; Teams is purely transport.

Errors are contained: a failure never raises out of ``handle_activity``; it
is audited and turned into an operator-safe reply so Teams gets a 200 (no
retry storm) and no stacktrace/secret/path leaks to the channel.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services import audit_service, auth, copilot_service

from . import adapter, files, graph, security
from .config import MsTeamsConfig, active_level, load_config
from .schemas import (
    ChannelResult,
    InboundTeamsEvent,
    InternalCopilotResponse,
    PermissionsContext,
)

logger = logging.getLogger("msteams.channel")

_MAX_TEXT = 8000  # hard cap before we even reach the copilot's own limit


async def handle_activity(
    activity: dict[str, Any],
    *,
    auth_header: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    cfg: MsTeamsConfig | None = None,
) -> ChannelResult:
    cfg = cfg or load_config()

    # ── Level 0: disabled → do nothing, say nothing. ──
    if cfg.is_level0():
        return ChannelResult(status="disabled")

    # ── Inbound JWT (Bot Framework) ──
    jwt_decision = security.verify_jwt(auth_header, cfg)
    if not jwt_decision.allowed:
        await _audit(None, None, "rejected", error_type=jwt_decision.reason)
        return ChannelResult(status="unauthorized", detail="unauthorized")

    # ── Parse transport facts (pure, never raises on odd payloads) ──
    try:
        event = adapter.parse_activity(activity)
    except Exception:  # defensive: malformed payload must not 500
        logger.warning("msteams: failed to parse activity", exc_info=True)
        await _audit(None, None, "bad_request", error_type="parse_failed")
        return ChannelResult(status="bad_request", detail="bad request")

    # Only handle message activities. Everything else (typing,
    # conversationUpdate, reactions…) is acknowledged silently.
    if event.activity_type != "message":
        return ChannelResult(status="ignored", detail="non_message_activity")

    # ── Adaptive Card button presses ──
    # Teams delivers card buttons as ``type=message`` with ``value`` populated
    # and empty ``text``. Route them BEFORE the empty-text guard so the
    # approval flow doesn't get swallowed as "ignored: empty_text".
    submit_value = adapter.adaptive_card_submit_value(activity)
    if submit_value is not None:
        return await _handle_approval_submit(event, cfg, submit_value)

    if not event.text.strip():
        await _audit(event, None, "ignored", error_type="empty_text")
        return ChannelResult(status="ignored", detail="empty_text")
    if len(event.text) > _MAX_TEXT:
        # Direct assignment (no model_copy revalidation) — can't raise on a
        # plain string slice, keeping the webhook contract (never 500).
        event.text = event.text[:_MAX_TEXT]

    # ── Authorization gates (tenant / dm|group policy / mention) ──
    decision, ctx = security.authorize(event, cfg)
    if not decision.allowed:
        await _audit(event, ctx, "rejected", error_type=decision.reason)
        # Silent for unauthorized senders — do not advertise the bot.
        return ChannelResult(status="unauthorized", detail="unauthorized")

    # ── Resolve the Teams sender to a console user (stable ids only) ──
    email = security.resolve_console_email(event, cfg)
    if not email:
        await _audit(event, ctx, "rejected", error_type="no_console_mapping")
        return ChannelResult(status="unauthorized", detail="unauthorized")

    try:
        console_user = await auth.get_user_by_email(email)
    except Exception:
        logger.warning("msteams: console user lookup failed", exc_info=True)
        await _audit(event, ctx, "error", error_type="user_lookup_failed")
        return ChannelResult(status="error", activity=adapter.error_activity(), detail="error")

    if not console_user or not console_user.get("id"):
        await _audit(event, ctx, "rejected", error_type="console_user_not_found")
        return ChannelResult(status="unauthorized", detail="unauthorized")
    if not console_user.get("is_active", True):
        # Distinct error_type so an operator can tell "user disabled" from
        # "no mapping in DB"; the user-facing response is the same.
        await _audit(event, ctx, "rejected", error_type="console_user_inactive")
        return ChannelResult(status="unauthorized", detail="unauthorized")

    # Guarded int() — a corrupt/non-numeric user id must not 500 the webhook
    # (Teams retries on non-2xx → duplicate deliveries).
    try:
        ctx.console_user_id = int(console_user["id"])
    except (TypeError, ValueError):
        logger.warning("msteams: console user id is not numeric (%s)", type(console_user.get("id")).__name__)
        await _audit(event, ctx, "error", error_type="invalid_user_id")
        return ChannelResult(status="error", activity=adapter.error_activity(), detail="error")
    ctx.console_user_email = console_user.get("email")
    ctx.level = active_level(cfg)

    # ── Optional file ingestion (operator opt-in via MSTEAMS_FILES_ENABLED) ──
    # When enabled, supported text attachments are downloaded via Graph and
    # appended to the user's prompt as a fenced footer. Each attachment
    # audits its own outcome ("file_attached" / "file_skipped_type" /
    # "file_skipped_size" / "file_download_failed") so an operator can see
    # what the copilot actually saw. Failures NEVER block the turn — the
    # copilot still runs on the user's text.
    file_excerpt = ""
    if cfg.files_enabled and event.raw_attachments:
        file_excerpt = await _ingest_attachments(event, ctx, cfg)

    # Normalize to the internal copilot contract (transport → InternalCopilotRequest).
    # Teams logic stops here; the copilot speaks this contract, not Teams JSON.
    req = adapter.to_internal_request(event, ctx)
    user_message = req.text
    if file_excerpt:
        user_message = f"{user_message}\n\n---\n{file_excerpt}" if user_message else file_excerpt

    # ── Drive the EXISTING copilot ──
    try:
        conversation_id = await _ensure_conversation(event, console_user)
    except ConversationFallbackError:
        # DB pool down AND ad-hoc fallback also failed → distinct audit type
        # so an operator can tell "DB outage" from "copilot turn raised".
        await _audit(event, ctx, "error", error_type="conversation_mapping_unavailable")
        return ChannelResult(status="error", activity=adapter.error_activity(), detail="error")
    try:
        turn = await copilot_service.run_turn(
            conversation_id=conversation_id,
            user_message=user_message,
            user=console_user,
            ip=ip,
            user_agent=user_agent or "msteams",
        )
    except Exception as exc:
        # Copilot failure — contain it. Log full detail server-side; the
        # channel reply and audit carry only a safe, generic message.
        logger.warning("msteams: copilot turn failed: %s", type(exc).__name__, exc_info=True)
        await _audit(event, ctx, "error", error_type="copilot_turn_failed")
        return ChannelResult(status="error", activity=adapter.error_activity(), detail="error")

    resp = _to_internal_response(turn)
    activity_out = adapter.from_copilot_response(resp, conversation_id=conversation_id)
    await _audit(event, ctx, "ok", route="copilot.run_turn")
    return ChannelResult(status="ok", activity=activity_out)


def _to_internal_response(turn: dict[str, Any]) -> InternalCopilotResponse:
    text = str(turn.get("reply") or "")
    requires_approval = bool(turn.get("requires_approval"))
    raw_citations = turn.get("citations")
    if isinstance(raw_citations, list):
        # Pydantic strictly validates ``list[dict[str, Any]]`` — a hostile
        # citation list with None / non-dict entries would raise here and
        # 500 the webhook (this runs OUTSIDE the copilot try/except). Filter
        # defensively before constructing the model.
        citations: list[dict[str, Any]] | None = [c for c in raw_citations if isinstance(c, dict)]
    else:
        citations = None
    raw_pending = turn.get("pending_actions") if requires_approval else None
    pending: list[dict[str, Any]] | None = None
    if isinstance(raw_pending, list):
        pending = [a for a in raw_pending if isinstance(a, dict)]
    message_id_raw = turn.get("message_id") if requires_approval else None
    message_id = str(message_id_raw) if message_id_raw else None
    return InternalCopilotResponse(
        text=text,
        citations=citations,
        requires_approval=requires_approval,
        requires_approval_message_id=message_id,
        pending_actions=pending,
    )


# ── Conversation continuity (Teams conversation → console conversation) ──

async def _ensure_conversation(event: InboundTeamsEvent, console_user: dict) -> str:
    """Map a Teams conversation to a console conversation, creating one on
    first contact. Backed by ``msteams_conversations`` (see migration 95).

    If the mapping store is unavailable we DEGRADE to a fresh conversation
    per turn rather than 500 — the bot keeps answering, just without
    cross-turn memory. This is logged, never silently masked as success.
    """
    teams_conv = event.conversation_id or f"adhoc:{event.user_id or 'unknown'}"
    user_id = int(console_user["id"])
    try:
        pool = await auth.pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT conversation_id FROM msteams_conversations "
                "WHERE teams_conversation_id = $1 AND console_user_id = $2",
                teams_conv, user_id,
            )
            if row and row.get("conversation_id"):
                return str(row["conversation_id"])
            created = await copilot_service.create_conversation(
                user_id=user_id,
                workspace_id=None,
                title=f"Teams · {event.mode()}",
            )
            conv_id = str(created["id"])
            # Race-safe upsert: two concurrent first-contacts (same user/teams
            # conversation) used to each create a console conversation while
            # ON CONFLICT DO NOTHING silently dropped the second insert,
            # leaving one console conversation orphaned. ``DO UPDATE …
            # RETURNING`` returns the row that ACTUALLY won the race so both
            # callers converge on the same id.
            winning_id = await conn.fetchval(
                """
                INSERT INTO msteams_conversations
                  (teams_conversation_id, console_user_id, conversation_id,
                   tenant_id, mode)
                VALUES ($1, $2, $3::uuid, $4, $5)
                ON CONFLICT (teams_conversation_id, console_user_id) DO UPDATE
                  SET updated_at = NOW()
                RETURNING conversation_id
                """,
                teams_conv, user_id, conv_id, event.tenant_id, event.mode(),
            )
            return str(winning_id) if winning_id else conv_id
    except Exception:
        # Mapping store is unavailable. Try once more to give the user a
        # fresh ad-hoc conversation; if THAT fails too, surface a distinct
        # error type so the operator can tell "DB down" from "copilot down".
        logger.warning("msteams: conversation mapping unavailable; using ad-hoc conversation", exc_info=True)
        try:
            created = await copilot_service.create_conversation(
                user_id=user_id, workspace_id=None, title=f"Teams · {event.mode()}",
            )
            return str(created["id"])
        except Exception:
            logger.warning("msteams: ad-hoc conversation creation also failed", exc_info=True)
            raise ConversationFallbackError("conversation_mapping_unavailable")


class ConversationFallbackError(RuntimeError):
    """Raised by _ensure_conversation when even the ad-hoc fallback fails.
    Distinguished from a generic copilot turn failure for audit clarity."""


# ── File ingestion (Level-4 partial: text attachments via Graph) ──

# Per-turn cap. Beyond this many attachments we audit "file_skipped_overflow"
# and stop — a chat user attaching 50 files is almost always wrong, and the
# LLM prompt budget caps out long before then anyway.
_MAX_ATTACHMENTS_PER_TURN = 5


async def _ingest_attachments(
    event: InboundTeamsEvent,
    ctx: PermissionsContext,
    cfg: MsTeamsConfig,
) -> str:
    """Download supported text attachments via Graph, decode, and render a
    fenced excerpt suitable for appending to the user's prompt.

    Each attachment audits its own outcome:
      * ``file_attached``        — content extracted and surfaced to copilot
      * ``file_skipped_type``    — unsupported content type / extension
      * ``file_skipped_size``    — server-declared or streamed > files_max_bytes
      * ``file_token_unavailable`` — Graph app-only token could not be obtained
      * ``file_download_failed`` — Graph download error (HTTP non-200, network)
      * ``file_decode_failed``   — bytes could not be decoded as text
      * ``file_skipped_overflow``— more attachments than the per-turn cap

    Returns the rendered excerpt (empty string when no attachment yielded
    text). Never raises — file ingestion must NEVER break a copilot turn.
    """
    summaries: list[dict[str, Any]] = []
    raw = [a for a in (event.raw_attachments or []) if isinstance(a, dict)]
    for idx, att in enumerate(raw):
        if idx >= _MAX_ATTACHMENTS_PER_TURN:
            await _audit_file(
                event, ctx, status="ignored", reason="file_skipped_overflow",
                name=str(att.get("name") or ""),
            )
            continue
        name = str(att.get("name") or "")
        content_type = str(att.get("contentType") or "")
        # Teams puts the Graph-compatible download URL in contentUrl. For
        # non-file activities (link previews, image cards, etc.) it can be a
        # public URL — we still require token-authenticated GET, so non-Graph
        # URLs simply fail at the download step.
        url = str(att.get("contentUrl") or "")
        if not files.is_supported_text(content_type, name):
            await _audit_file(
                event, ctx, status="ignored", reason="file_skipped_type",
                name=name, content_type=content_type,
            )
            continue
        if not url:
            await _audit_file(
                event, ctx, status="ignored", reason="file_skipped_no_url",
                name=name, content_type=content_type,
            )
            continue
        result = await graph.download_attachment(
            url, cfg=cfg, max_bytes=cfg.files_max_bytes,
        )
        if not result.ok:
            # Granular audit so an operator can distinguish "token broken"
            # from "file too big" from "Microsoft Graph 404'd".
            status = "ignored" if result.reason == "download_too_large" else "error"
            await _audit_file(
                event, ctx, status=status, reason=f"file_{result.reason}",
                name=name, content_type=content_type,
            )
            continue
        text = files.decode_text(result.content or b"", result.content_type)
        if text is None:
            await _audit_file(
                event, ctx, status="error", reason="file_decode_failed",
                name=name, content_type=content_type,
            )
            continue
        # Per-attachment char cap before we hand it to the renderer (which
        # also enforces a total budget across all attachments).
        excerpt = text[: files.TEXT_BUDGET_CHARS]
        summaries.append({"name": name or "archivo", "text": excerpt})
        await _audit_file(
            event, ctx, status="ok", reason="file_attached",
            name=name, content_type=content_type,
            bytes_len=len(result.content or b""), text_len=len(excerpt),
        )
    return files.render_for_copilot(summaries)


async def _audit_file(
    event: InboundTeamsEvent,
    ctx: PermissionsContext,
    *,
    status: str,
    reason: str,
    name: str = "",
    content_type: str = "",
    bytes_len: int | None = None,
    text_len: int | None = None,
) -> None:
    """Audit a per-attachment outcome with action="msteams.file".

    Privacy: we record the file NAME (operator forensics — "who tried to
    upload payroll.csv") plus lengths, NEVER the file contents.
    """
    try:
        metadata: dict[str, Any] = {
            "channel": "msteams",
            "tenant_id": event.tenant_id,
            "teams_user_id": event.aad_object_id or event.user_id,
            "teams_user_name": event.user_name,
            "conversation_id": event.conversation_id,
            "message_id": event.message_id,
            "mode": event.mode(),
            "file_name": name,
            "file_content_type": content_type,
            "reason": reason,
        }
        if bytes_len is not None:
            metadata["file_bytes_len"] = bytes_len
        if text_len is not None:
            metadata["file_text_len"] = text_len
        await audit_service.record_event(
            user_id=ctx.console_user_id,
            email=ctx.console_user_email,
            action="msteams.file",
            resource_type="msteams",
            resource_id=event.conversation_id,
            status=status,
            metadata=metadata,
            conversation_id=None,
        )
    except Exception:
        logger.warning("msteams: file audit write failed", exc_info=True)


# ── Adaptive Card approval flow ──

# Valid intents emitted by ``adapter._approval_card``. Any other intent on a
# Card Submit is an unknown/forged button — we audit and ignore (never raise).
_APPROVAL_INTENT_APPROVE = "msteams.approval.approve"
_APPROVAL_INTENT_REJECT = "msteams.approval.reject"


async def _handle_approval_submit(
    event: InboundTeamsEvent,
    cfg: MsTeamsConfig,
    value: dict[str, Any],
) -> ChannelResult:
    """Process an Adaptive Card button press from the approval prompt.

    Re-runs the SAME gates as a regular message (tenant / dm|group policy,
    console-user mapping) so the button cannot bypass authorization, then
    delegates to ``copilot_service.approve_pending_action`` — the SAME
    service entrypoint the console UI calls. There is no parallel approval
    surface; the channel only renders the button and routes the press.

    Trust model for the button payload:
      * ``conversation_id`` / ``message_id`` come from the client and MUST
        NOT be trusted on their own. ``approve_pending_action`` enforces
        conversation-ownership (``conv.user_id == user.id``), so an attacker
        crafting a payload for someone else's pending action is rejected
        inside the copilot service. The Teams identity is still established
        by the Bot Framework JWT + AAD allowlist.
      * ``intent`` is whitelisted here; any other value is audited as a
        forged button and ignored.
    """
    intent = str(value.get("intent") or "")
    conv_id = str(value.get("conversation_id") or "")
    msg_id = str(value.get("message_id") or "")

    if intent not in {_APPROVAL_INTENT_APPROVE, _APPROVAL_INTENT_REJECT}:
        await _audit(event, None, "ignored", error_type="approval_unknown_intent")
        return ChannelResult(status="ignored", detail="approval_unknown_intent")
    if not (conv_id and msg_id):
        await _audit(event, None, "bad_request", error_type="approval_missing_ids")
        return ChannelResult(status="bad_request", detail="bad_request")

    # Same authorization gates as for messages — a Card button is not
    # privileged over a text message.
    decision, ctx = security.authorize(event, cfg)
    if not decision.allowed:
        await _audit(event, ctx, "rejected", error_type=decision.reason)
        return ChannelResult(status="unauthorized", detail="unauthorized")

    email = security.resolve_console_email(event, cfg)
    if not email:
        await _audit(event, ctx, "rejected", error_type="no_console_mapping")
        return ChannelResult(status="unauthorized", detail="unauthorized")

    try:
        console_user = await auth.get_user_by_email(email)
    except Exception:
        logger.warning("msteams: console user lookup failed", exc_info=True)
        await _audit(event, ctx, "error", error_type="user_lookup_failed")
        return ChannelResult(status="error", activity=adapter.error_activity(), detail="error")
    if not console_user or not console_user.get("id"):
        await _audit(event, ctx, "rejected", error_type="console_user_not_found")
        return ChannelResult(status="unauthorized", detail="unauthorized")
    if not console_user.get("is_active", True):
        await _audit(event, ctx, "rejected", error_type="console_user_inactive")
        return ChannelResult(status="unauthorized", detail="unauthorized")

    try:
        ctx.console_user_id = int(console_user["id"])
    except (TypeError, ValueError):
        await _audit(event, ctx, "error", error_type="invalid_user_id")
        return ChannelResult(status="error", activity=adapter.error_activity(), detail="error")
    ctx.console_user_email = console_user.get("email")

    # ── Reject branch: audit-only. No copilot side-effects; the pending row
    # stays pending so an operator can still resolve it from the UI. We do
    # NOT mark the row "rejected" from here because doing so would push a
    # state-mutation API into the channel (it'd need its own DB write); the
    # console UI is the system of record for that side-effect.
    if intent == _APPROVAL_INTENT_REJECT:
        await audit_service_record(
            event, ctx, status="rejected", route="msteams.approval.reject",
            extra={"approval_conversation_id": conv_id, "approval_message_id": msg_id},
        )
        return ChannelResult(
            status="ok",
            activity=adapter.approval_outcome_card(
                status="rejected",
                summary="Pendiente sin ejecutar. Puedes resolverla desde la consola.",
            ),
        )

    # ── Approve branch: route through copilot_service.approve_pending_action
    # The same function the console UI calls. Conversation ownership is
    # enforced INSIDE that function — a button payload referencing another
    # user's conversation is rejected there.
    try:
        result = await copilot_service.approve_pending_action(
            conversation_id=conv_id,
            message_id=msg_id,
            user=console_user,
        )
    except Exception as exc:
        logger.warning(
            "msteams: approval routing failed: %s", type(exc).__name__, exc_info=True,
        )
        await audit_service_record(
            event, ctx, status="error", route="msteams.approval.approve",
            extra={"approval_message_id": msg_id, "error_type": "approval_failed"},
        )
        return ChannelResult(
            status="error",
            activity=adapter.approval_outcome_card(
                status="failed",
                summary="No se pudo registrar la aprobación. Inténtalo desde la consola.",
            ),
            detail="error",
        )

    await audit_service_record(
        event, ctx, status="ok", route="msteams.approval.approve",
        extra={"approval_message_id": msg_id},
    )
    # Surface a short tail of the copilot's reply (if any) so the user sees
    # the immediate outcome, without bringing the full conversation into
    # the card.
    summary = ""
    if isinstance(result, dict):
        summary = str(result.get("reply") or "")[:300]
    return ChannelResult(
        status="ok",
        activity=adapter.approval_outcome_card(status="approved", summary=summary or None),
    )


async def audit_service_record(
    event: InboundTeamsEvent,
    ctx: PermissionsContext,
    *, status: str, route: str, extra: dict[str, Any],
) -> None:
    """Audit an approval-flow decision with the same envelope as ``_audit``
    plus the approval-specific keys. Errors are swallowed (audit must never
    break a webhook reply)."""
    try:
        metadata: dict[str, Any] = {
            "channel": "msteams",
            "tenant_id": event.tenant_id,
            "teams_user_id": event.aad_object_id or event.user_id,
            "teams_user_name": event.user_name,
            "conversation_id": event.conversation_id,
            "message_id": event.message_id,
            "mode": event.mode(),
            "route": route,
        }
        metadata.update(extra or {})
        await audit_service.record_event(
            user_id=ctx.console_user_id,
            email=ctx.console_user_email,
            action="msteams.approval",
            resource_type="msteams",
            resource_id=event.conversation_id,
            status=status,
            metadata=metadata,
            conversation_id=None,
        )
    except Exception:
        logger.warning("msteams: approval audit write failed", exc_info=True)


# ── Audit (reuses the platform audit_service; no new table) ──

async def _audit(
    event: InboundTeamsEvent | None,
    ctx: PermissionsContext | None,
    status: str,
    *,
    route: str | None = None,
    error_type: str | None = None,
) -> None:
    try:
        metadata: dict[str, Any] = {"channel": "msteams"}
        if event is not None:
            metadata.update({
                "tenant_id": event.tenant_id,
                "teams_user_id": event.aad_object_id or event.user_id,
                # Display name is audited for UX / forensic readability ("which
                # human did this") but is NEVER consulted for authorization —
                # auth is exclusively by stable AAD object id. The name is
                # attacker-influenceable (Teams lets users edit their own
                # display name), so trusting it as a security control would
                # be a known anti-pattern. See the security-model section of
                # the channel README.
                "teams_user_name": event.user_name,
                "conversation_id": event.conversation_id,
                "message_id": event.message_id,
                "mode": event.mode(),
                # Privacy: store length only, never the raw message text.
                "text_len": len(event.text or ""),
            })
        if route:
            metadata["route"] = route
        if error_type:
            metadata["error_type"] = error_type
        await audit_service.record_event(
            user_id=(ctx.console_user_id if ctx else None),
            email=(ctx.console_user_email if ctx else None),
            action="msteams.message",
            resource_type="msteams",
            resource_id=(event.conversation_id if event else None),
            status=status,
            metadata=metadata,
            conversation_id=None,
        )
    except Exception:  # auditing must never break the channel
        logger.warning("msteams: audit write failed", exc_info=True)


def status_snapshot(cfg: MsTeamsConfig | None = None) -> dict[str, Any]:
    """Operator-facing, secret-free status for the admin status endpoint."""
    cfg = cfg or load_config()
    return {
        "channel": "msteams",
        "enabled": cfg.enabled,
        "level": active_level(cfg),
        "webhook_path": cfg.webhook_path,
        "dm_policy": cfg.dm_policy,
        "group_policy": cfg.group_policy,
        "require_mention": cfg.require_mention,
        "jwt_validation": cfg.jwt_mode,
        "allowed_users_count": len(cfg.allowed_users),
        "allowed_conversations_count": len(cfg.allowed_conversations),
        "allowed_tenants_count": len(cfg.allowed_tenants),
        "user_map_count": len(cfg.user_map),
        "has_default_user": bool(cfg.default_user_email),
        "graph_enabled": cfg.graph_enabled,
        "transcripts_enabled": cfg.transcripts_enabled,
        "files_enabled": cfg.files_enabled,
        "files_max_bytes": cfg.files_max_bytes,
        # Never expose app_password / tenant secrets.
        "app_id_configured": bool(cfg.app_id),
    }
