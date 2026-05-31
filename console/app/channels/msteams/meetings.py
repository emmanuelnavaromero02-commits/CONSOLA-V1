"""Microsoft Teams channel — Level 3 (post-meeting) scaffolding.

DESIGN-ONLY for v0.1. Reading meeting transcripts requires Microsoft Graph
application permissions AND the tenant having transcription enabled — both
admin-gated in Microsoft 365 / Entra. None of that is wired here.

These interfaces exist so the pipeline shape is fixed and the rest of the
platform can depend on it, while every entry point FAILS CLOSED with a
clear status (never an exception, never fabricated meeting data) until an
operator enables ``MSTEAMS_GRAPH_ENABLED`` + ``MSTEAMS_TRANSCRIPTS_ENABLED``
and a future PR implements the Graph calls.

Pipeline (future):
  meeting_ingestion → transcript_ingestion → attendance_analyzer
                    → minutes_generator → copilot knowledge base.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import MsTeamsConfig, load_config


@dataclass(frozen=True)
class CapabilityStatus:
    """Result of asking a Level-3 capability to run. ``available`` is False
    whenever a prerequisite flag/permission is missing; ``reason`` is an
    operator-safe explanation. ``data`` is only populated by a future real
    implementation."""
    capability: str
    available: bool
    reason: str
    data: dict[str, Any] = field(default_factory=dict)


def _gate(capability: str, cfg: MsTeamsConfig) -> CapabilityStatus | None:
    """Shared precondition gate. Returns a closed status when a prerequisite
    is missing, or None when prerequisites pass (→ caller proceeds, which in
    v0.1 still returns a 'not implemented' status — honestly)."""
    if not cfg.enabled:
        return CapabilityStatus(capability, False, "channel_disabled")
    if not cfg.graph_enabled:
        return CapabilityStatus(capability, False, "graph_disabled")
    if not cfg.transcripts_enabled:
        return CapabilityStatus(capability, False, "transcripts_disabled")
    return None


def meeting_ingestion(meeting_id: str, *, cfg: MsTeamsConfig | None = None) -> CapabilityStatus:
    """Detect/authorize a meeting for post-event processing."""
    cfg = cfg or load_config()
    closed = _gate("meeting_ingestion", cfg)
    if closed:
        return closed
    return CapabilityStatus(
        "meeting_ingestion", False,
        "not_implemented: Graph meeting lookup is a future PR",
    )


def transcript_ingestion(meeting_id: str, *, cfg: MsTeamsConfig | None = None) -> CapabilityStatus:
    """Fetch a meeting transcript via Graph IF authorized and present.

    Returns a clear status — never raises — when transcripts aren't enabled
    or none exist. The caller must treat ``available=False`` as 'no
    transcript to read', not an error.
    """
    cfg = cfg or load_config()
    closed = _gate("transcript_ingestion", cfg)
    if closed:
        return closed
    return CapabilityStatus(
        "transcript_ingestion", False,
        "not_implemented: Graph transcript fetch is a future PR",
    )


def attendance_analyzer(meeting_id: str, *, cfg: MsTeamsConfig | None = None) -> CapabilityStatus:
    """Analyze attendance (who joined/left, late/early) when Graph allows.

    Note: join/leave precision depends on whether the tenant exposes the
    attendance report — when it doesn't, a future implementation reports the
    coarse roster only, never invented timings.
    """
    cfg = cfg or load_config()
    closed = _gate("attendance_analyzer", cfg)
    if closed:
        return closed
    return CapabilityStatus(
        "attendance_analyzer", False,
        "not_implemented: attendance analysis is a future PR",
    )


def minutes_generator(meeting_id: str, *, cfg: MsTeamsConfig | None = None) -> CapabilityStatus:
    """Derive agreements, decisions, tasks, owners, dates, risks and open
    questions from an ingested transcript, then persist to the copilot KB.

    v0.1 returns closed; the future implementation routes the transcript
    through the existing copilot/LLM (never a bespoke model here)."""
    cfg = cfg or load_config()
    closed = _gate("minutes_generator", cfg)
    if closed:
        return closed
    return CapabilityStatus(
        "minutes_generator", False,
        "not_implemented: minutes generation is a future PR",
    )


def capabilities_status(cfg: MsTeamsConfig | None = None) -> dict[str, Any]:
    """Snapshot of Level-3 readiness for the status endpoint / diagnostics."""
    cfg = cfg or load_config()
    probe = transcript_ingestion("__probe__", cfg=cfg)
    return {
        "graph_enabled": cfg.graph_enabled,
        "transcripts_enabled": cfg.transcripts_enabled,
        "sharepoint_configured": bool(cfg.sharepoint_site_id),
        "post_meeting_available": probe.available,
        "post_meeting_reason": probe.reason,
    }
