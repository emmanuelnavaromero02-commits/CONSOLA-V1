"""Normalized contracts for the Teams channel.

Teams activity payloads are large, deeply nested and provider-specific.
We never let that shape leak into the copilot: the adapter collapses an
inbound Teams activity into ``InboundTeamsEvent`` (transport-level facts)
and then into ``InternalCopilotRequest`` (what the copilot router speaks).
Outbound, the copilot answers with ``InternalCopilotResponse`` which the
adapter renders back to a Teams activity dict.

These are intentionally small and explicit so business logic never has to
parse Teams JSON.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ConversationMode = Literal["dm", "channel", "group", "meeting"]


class InboundTeamsEvent(BaseModel):
    """Transport-level facts extracted from a Teams Bot Framework activity.

    Only stable identifiers — never display names — are load-bearing. A
    display name (``user_name``) is carried for audit readability only and
    MUST NOT be used for authorization.
    """
    activity_type: str = "message"
    tenant_id: str | None = None
    aad_object_id: str | None = None  # stable user id (preferred)
    user_id: str | None = None        # Bot Framework from.id (fallback)
    user_name: str | None = None      # display name — NOT for auth
    conversation_id: str | None = None
    conversation_type: str | None = None  # personal | channel | groupChat
    message_id: str | None = None
    text: str = ""
    service_url: str | None = None
    recipient_id: str | None = None   # the bot's own id, for mention stripping
    mentioned_bot: bool = False
    raw_attachments: list[dict[str, Any]] = Field(default_factory=list)

    def mode(self) -> ConversationMode:
        ctype = (self.conversation_type or "").lower()
        if ctype == "personal":
            return "dm"
        if ctype == "channel":
            return "channel"
        if ctype in {"groupchat", "group"}:
            return "group"
        return "dm"


class PermissionsContext(BaseModel):
    """What the channel resolved about the sender's authorization. Passed to
    the copilot so downstream can reason about provenance without re-deriving."""
    source: str = "msteams"
    tenant_allowed: bool = False
    user_allowed: bool = False
    conversation_allowed: bool = False
    console_user_id: int | None = None
    console_user_email: str | None = None
    level: int = 0


class InternalCopilotRequest(BaseModel):
    source: Literal["msteams"] = "msteams"
    tenant_id: str | None = None
    user_id: str | None = None  # the Teams stable id (provenance)
    conversation_id: str | None = None
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    permissions_context: PermissionsContext = Field(default_factory=PermissionsContext)


class InternalCopilotResponse(BaseModel):
    text: str = ""
    blocks: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    files: list[dict[str, Any]] | None = None
    citations: list[dict[str, Any]] | None = None
    error: str | None = None


class ChannelResult(BaseModel):
    """What the route returns. ``status`` drives the audit record and the
    HTTP code. ``activity`` is the Teams-shaped reply (may be None when we
    intentionally stay silent, e.g. ignored/unauthorized)."""
    status: Literal[
        "ok", "ignored", "disabled", "unauthorized", "bad_request", "error"
    ]
    activity: dict[str, Any] | None = None
    detail: str | None = None  # operator-safe message; never secrets/stacktrace
