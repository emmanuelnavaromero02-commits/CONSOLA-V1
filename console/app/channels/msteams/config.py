"""Microsoft Teams channel — configuration & capability levels.

This module owns ALL environment reading for the Teams channel. Nothing
else in the channel touches ``os.environ`` directly, so the security and
service layers stay testable (monkeypatch this module's getters).

Capability levels (see README):
  0 — Disabled: MSTEAMS_ENABLED=false → the channel ignores everything.
  1 — Basic bot: authorised users DM the copilot and get a reply.
  2 — Allowlisted conversations: admin-scoped users/teams/channels.
  3 — Post-meeting (Graph + transcripts) — scaffolded, OFF by default.
  4 — Advanced (files, SharePoint, adaptive cards) — interfaces only.

Security defaults are deliberately closed: disabled channel, allowlist
DM/group policies, name-matching off. An empty allowlist with an
``allowlist`` policy means "nobody", never "everybody".
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

_logger = logging.getLogger("msteams.config")

_TRUE = {"1", "true", "yes", "on"}
_VALID_DM_POLICIES = {"allowlist", "open", "disabled"}
_VALID_GROUP_POLICIES = {"allowlist", "open", "disabled"}
_VALID_JWT_MODES = {"claims", "strict", "disabled"}


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in _TRUE


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _csv(name: str) -> tuple[str, ...]:
    """Parse a comma/space separated allowlist into a tuple of stable ids.

    Blank entries are dropped. We DO NOT lowercase: AAD object ids are
    case-insensitive GUIDs but conversation ids are case-sensitive, so we
    compare case-insensitively at match time instead of mangling here.
    """
    raw = os.environ.get(name, "") or ""
    # Normalise whitespace (including tabs) to commas, then split + strip.
    # Dedup preserves first-seen order so a duplicated id is silently
    # collapsed rather than amplifying allowlist iteration cost.
    normalised = raw.replace("\n", ",").replace("\t", ",").replace(" ", ",")
    seen: set[str] = set()
    out: list[str] = []
    for p in (q.strip() for q in normalised.split(",")):
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return tuple(out)


@dataclass(frozen=True)
class MsTeamsConfig:
    enabled: bool
    app_id: str
    app_password: str
    tenant_id: str
    webhook_path: str
    require_mention: bool
    dm_policy: str
    group_policy: str
    allowed_users: tuple[str, ...]
    allowed_conversations: tuple[str, ...]
    allowed_tenants: tuple[str, ...]
    # Identity bridge: maps a Teams AAD object id → console user email.
    user_map: dict[str, str]
    default_user_email: str
    # JWT posture: "claims" (validate iss/aud/tid/exp, no signature),
    # "strict" (require a wired signature verifier, else fail closed),
    # "disabled" (no inbound auth — local dev only).
    jwt_mode: str
    # Level 3+ feature flags (OFF by default).
    graph_enabled: bool
    transcripts_enabled: bool
    sharepoint_site_id: str

    def is_level0(self) -> bool:
        return not self.enabled


def _parse_user_map(raw: str) -> dict[str, str]:
    """Parse ``aadObjectId=email,aadObjectId2=email2`` → dict.

    Malformed pairs are skipped silently (config must never crash boot).
    Duplicate keys log a warning instead of last-write-wins so a typo
    doesn't quietly route a Teams user to the wrong console identity.
    Emails are lowercased to align with case-insensitive DB lookups
    (RFC 5321 makes local-part formally case-sensitive but every
    mainstream user store treats it case-insensitively).
    """
    out: dict[str, str] = {}
    for pair in (raw or "").replace("\n", ",").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, val = pair.partition("=")
        key, val = key.strip().lower(), val.strip().lower()
        if not (key and val):
            continue
        if key in out and out[key] != val:
            # PII hygiene: do NOT log the conflicting email values. Emails are
            # personal identifiers (GDPR); a duplicate-key misconfig would
            # otherwise leak BOTH mapped emails into application logs every
            # time the config is parsed. The AAD key alone is enough for an
            # operator to find and fix the typo in MSTEAMS_USER_MAP.
            _logger.warning(
                "msteams: duplicate MSTEAMS_USER_MAP key %r; first mapping kept (emails redacted)",
                key,
            )
            continue
        out.setdefault(key, val)
    return out


def load_config() -> MsTeamsConfig:
    """Read the channel config from the environment. Cheap; call per request
    so a hot-reload of env (tests, /config) is reflected without a restart."""
    dm_policy = _str("MSTEAMS_DM_POLICY", "allowlist").lower()
    if dm_policy not in _VALID_DM_POLICIES:
        dm_policy = "allowlist"
    group_policy = _str("MSTEAMS_GROUP_POLICY", "allowlist").lower()
    if group_policy not in _VALID_GROUP_POLICIES:
        group_policy = "allowlist"
    jwt_mode = _str("MSTEAMS_JWT_VALIDATION", "claims").lower()
    if jwt_mode not in _VALID_JWT_MODES:
        jwt_mode = "claims"

    return MsTeamsConfig(
        enabled=_flag("MSTEAMS_ENABLED", "false"),
        app_id=_str("MSTEAMS_APP_ID"),
        app_password=_str("MSTEAMS_APP_PASSWORD"),
        tenant_id=_str("MSTEAMS_TENANT_ID"),
        webhook_path=_str("MSTEAMS_WEBHOOK_PATH", "/api/msteams/messages") or "/api/msteams/messages",
        require_mention=_flag("MSTEAMS_REQUIRE_MENTION", "true"),
        dm_policy=dm_policy,
        group_policy=group_policy,
        allowed_users=_csv("MSTEAMS_ALLOWED_USERS"),
        allowed_conversations=_csv("MSTEAMS_ALLOWED_CONVERSATIONS"),
        allowed_tenants=_csv("MSTEAMS_ALLOWED_TENANTS"),
        user_map=_parse_user_map(os.environ.get("MSTEAMS_USER_MAP", "")),
        default_user_email=_str("MSTEAMS_DEFAULT_USER_EMAIL"),
        jwt_mode=jwt_mode,
        graph_enabled=_flag("MSTEAMS_GRAPH_ENABLED", "false"),
        transcripts_enabled=_flag("MSTEAMS_TRANSCRIPTS_ENABLED", "false"),
        sharepoint_site_id=_str("MSTEAMS_SHAREPOINT_SITE_ID"),
    )


def active_level(cfg: MsTeamsConfig | None = None) -> int:
    """Report the highest capability level the current config unlocks.
    Purely informational (status endpoint / audit), never a security gate."""
    cfg = cfg or load_config()
    if not cfg.enabled:
        return 0
    if cfg.graph_enabled and cfg.transcripts_enabled:
        return 3
    # Level 2 means explicit allowlist SCOPING is in effect — either an
    # operator-declared allowlist OR an explicit "trust the members"
    # decision (group_policy=open). An allowlist policy with no entries
    # is functionally Level 1 (DM-only) for the user, so don't inflate.
    if cfg.allowed_conversations or cfg.allowed_users or cfg.group_policy == "open":
        return 2
    return 1
