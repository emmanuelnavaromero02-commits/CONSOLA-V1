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

from . import adapter, security
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

    # Only handle message activities with real text. Everything else
    # (typing, conversationUpdate, reactions…) is acknowledged silently.
    if event.activity_type != "message":
        return ChannelResult(status="ignored", detail="non_message_activity")
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

    # Normalize to the internal copilot contract (transport → InternalCopilotRequest).
    # Teams logic stops here; the copilot speaks this contract, not Teams JSON.
    req = adapter.to_internal_request(event, ctx)

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
            user_message=req.text,
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
    activity_out = adapter.from_copilot_response(resp)
    await _audit(event, ctx, "ok", route="copilot.run_turn")
    return ChannelResult(status="ok", activity=activity_out)


def _to_internal_response(turn: dict[str, Any]) -> InternalCopilotResponse:
    text = str(turn.get("reply") or "")
    if turn.get("requires_approval"):
        text = (text + "\n\n_Esta acción requiere aprobación humana y debe aprobarse "
                "desde la consola; Teams no aprueba acciones en esta versión._").strip()
    raw_citations = turn.get("citations")
    if isinstance(raw_citations, list):
        # Pydantic strictly validates ``list[dict[str, Any]]`` — a hostile
        # citation list with None / non-dict entries would raise here and
        # 500 the webhook (this runs OUTSIDE the copilot try/except). Filter
        # defensively before constructing the model.
        citations: list[dict[str, Any]] | None = [c for c in raw_citations if isinstance(c, dict)]
    else:
        citations = None
    return InternalCopilotResponse(text=text, citations=citations)


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
        # Never expose app_password / tenant secrets.
        "app_id_configured": bool(cfg.app_id),
    }
