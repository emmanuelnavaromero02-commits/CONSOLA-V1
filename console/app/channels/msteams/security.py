"""Microsoft Teams channel — access control.

Default-closed. Every gate returns a ``(allowed, reason)`` decision; the
service turns a denial into a silent/unauthorized result + audit, never an
exception that could leak internals.

Identifiers: we authorize on STABLE ids only (AAD object id, Bot Framework
conversation id). Display-name matching is OFF unless an operator flips the
explicit dangerous switch — names are mutable and attacker-influenced.

JWT: Teams calls the webhook with a Bot Framework JWT. Modes:
  * ``claims``  — decode WITHOUT signature verification and validate
                  audience (== app id) + tenant + expiry. Honest posture
                  for a disabled-by-default Level-1 bot; documented as NOT
                  cryptographically verifying the signature.
  * ``strict``  — require a wired signature verifier (JWKS). None is wired
                  in this version, so strict FAILS CLOSED (rejects all).
                  This is the production setting once JWKS is implemented.
  * ``disabled``— no inbound auth. Local dev / behind a trusted gateway only.
"""
from __future__ import annotations

import time
from typing import Any

from .config import MsTeamsConfig
from .schemas import InboundTeamsEvent, PermissionsContext

# Bot Framework token issuers (public cloud). Used by claims validation.
_BOTFRAMEWORK_ISSUERS = (
    "https://api.botframework.com",
    "https://login.botframework.com",
)


def _eq_id(a: str | None, b: str | None) -> bool:
    """Case-insensitive identifier equality (AAD GUIDs are case-insensitive;
    conversation ids compared the same way for robustness)."""
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


def _in_allowlist(value: str | None, allowlist: tuple[str, ...]) -> bool:
    return any(_eq_id(value, item) for item in allowlist)


class Decision:
    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str = "") -> None:
        self.allowed = allowed
        self.reason = reason

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed


def verify_jwt(auth_header: str | None, cfg: MsTeamsConfig) -> Decision:
    """Validate the inbound Bot Framework token per the configured mode."""
    if cfg.jwt_mode == "disabled":
        return Decision(True, "jwt_disabled")

    token = ""
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        return Decision(False, "missing_bearer_token")

    if cfg.jwt_mode == "strict":
        verifier = _signature_verifier()
        if verifier is None:
            # Fail closed: no JWKS signature verifier wired in this version.
            return Decision(False, "strict_signature_verification_unavailable")
        return verifier(token, cfg)

    # claims mode — decode without signature verification, validate claims.
    return _validate_claims(token, cfg)


def _signature_verifier():
    """Return a JWKS signature verifier callable, or None if unavailable.

    NOTE: full Bot Framework JWKS signature verification is a documented
    hardening item (see README "JWT signature verification"). It is NOT
    wired here, so ``strict`` mode fails closed rather than pretending to
    verify. A future PR plugs the verifier in here.
    """
    return None


def _validate_claims(token: str, cfg: MsTeamsConfig) -> Decision:
    try:
        # Use python-jose (already in console/requirements.txt). We want the
        # claims WITHOUT signature verification — claims mode validates
        # iss/aud/exp structurally and is documented as weaker than strict.
        # ``get_unverified_claims`` returns the payload without touching the
        # signature or asking for a key/algorithm.
        from jose import jwt as _jwt
    except Exception:
        return Decision(False, "jwt_library_unavailable")
    try:
        claims: dict[str, Any] = _jwt.get_unverified_claims(token)
    except Exception:
        return Decision(False, "jwt_decode_failed")

    now = int(time.time())
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and now > int(exp) + 60:
        return Decision(False, "jwt_expired")

    iss = str(claims.get("iss") or "")
    if iss and not any(iss.startswith(known) for known in _BOTFRAMEWORK_ISSUERS):
        # Unknown issuer is suspicious; only enforce when we have an issuer.
        return Decision(False, "jwt_unknown_issuer")

    # Audience must be our app id when we know it.
    if cfg.app_id:
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if not any(_eq_id(str(a), cfg.app_id) for a in auds if a):
            return Decision(False, "jwt_audience_mismatch")
    return Decision(True, "claims_validated")


def authorize(event: InboundTeamsEvent, cfg: MsTeamsConfig) -> tuple[Decision, PermissionsContext]:
    """Apply enabled + tenant + dm/group + mention gates.

    Returns the decision plus the permissions context (partially filled;
    the service fills ``console_user_*`` after identity resolution).
    """
    ctx = PermissionsContext(level=0)

    if cfg.is_level0():
        return Decision(False, "channel_disabled"), ctx

    # ── Tenant allowlist (if configured) ──
    if cfg.allowed_tenants:
        if not _in_allowlist(event.tenant_id, cfg.allowed_tenants):
            return Decision(False, "tenant_not_allowed"), ctx
    ctx.tenant_allowed = True

    mode = event.mode()

    if mode == "dm":
        decision = _authorize_dm(event, cfg, ctx)
    else:  # channel / group / meeting share the group policy + mention rule
        decision = _authorize_group(event, cfg, ctx)
    return decision, ctx


def _authorize_dm(event: InboundTeamsEvent, cfg: MsTeamsConfig, ctx: PermissionsContext) -> Decision:
    if cfg.dm_policy == "disabled":
        return Decision(False, "dm_disabled")
    if cfg.dm_policy == "open":
        ctx.user_allowed = True
        ctx.conversation_allowed = True
        return Decision(True, "dm_open")
    # allowlist
    if _in_allowlist(event.aad_object_id, cfg.allowed_users) or _in_allowlist(event.user_id, cfg.allowed_users):
        ctx.user_allowed = True
        ctx.conversation_allowed = True
        return Decision(True, "dm_allowlisted")
    return Decision(False, "user_not_allowed")


def _authorize_group(event: InboundTeamsEvent, cfg: MsTeamsConfig, ctx: PermissionsContext) -> Decision:
    if cfg.group_policy == "disabled":
        return Decision(False, "group_disabled")

    # Mention requirement (default true) — the bot only acts when @mentioned.
    if cfg.require_mention and not event.mentioned_bot:
        return Decision(False, "mention_required")

    if cfg.group_policy == "allowlist":
        # Default-closed: BOTH gates must pass.
        #   1. The conversation must be explicitly allowlisted.
        #   2. The sender must be in allowed_users (stable id). An EMPTY
        #      allowed_users therefore means NOBODY — symmetric with the DM
        #      path and consistent with "empty allowlist + allowlist = closed".
        # To trust every member of a channel instead, use group_policy="open"
        # (still mention-gated). We deliberately do NOT treat an empty user
        # allowlist as "any member" — that would be an accidental open door.
        if not _in_allowlist(event.conversation_id, cfg.allowed_conversations):
            return Decision(False, "conversation_not_allowed")
        ctx.conversation_allowed = True
        if not (
            _in_allowlist(event.aad_object_id, cfg.allowed_users)
            or _in_allowlist(event.user_id, cfg.allowed_users)
        ):
            return Decision(False, "user_not_allowed")
        ctx.user_allowed = True
        return Decision(True, "group_allowlisted")

    # group_policy == "open" (still mention-gated above): any tenant member
    # in the conversation may trigger the bot. This is the explicit
    # "trust the channel members" mode.
    ctx.conversation_allowed = True
    ctx.user_allowed = True
    return Decision(True, "group_open")


def resolve_console_email(event: InboundTeamsEvent, cfg: MsTeamsConfig) -> str | None:
    """Map the Teams sender to a console user EMAIL using stable ids only.

    Priority: explicit user_map[aad_object_id] → user_map[user_id] →
    configured default service account. Display names are never used.
    Returns None when no mapping exists (→ unauthorized, never a guess).
    """
    if event.aad_object_id:
        mapped = cfg.user_map.get(event.aad_object_id.strip().lower())
        if mapped:
            return mapped
    if event.user_id:
        mapped = cfg.user_map.get(event.user_id.strip().lower())
        if mapped:
            return mapped
    if cfg.default_user_email:
        return cfg.default_user_email
    return None
