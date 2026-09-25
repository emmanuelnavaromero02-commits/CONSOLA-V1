from __future__ import annotations

from pydantic import Field

from app.schemas.control_room_public_projection import PublicProjectionModel


class AgentMemoryEvidenceFilters(PublicProjectionModel):
    subject: str | None = None
    active_only: bool | None = None
    limit: int | None = None


class AgentMemoryEvidenceRef(PublicProjectionModel):
    type: str | None = None
    source: str | None = None
    filters: AgentMemoryEvidenceFilters = Field(
        default_factory=AgentMemoryEvidenceFilters
    )


class AgentMemoryFinding(PublicProjectionModel):
    subject: str | None = None
    finding_type: str | None = None
    summary: str | None = None
    severity: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    recorded_by: str | None = None


class ControlRoomAgentMemoryResponse(PublicProjectionModel):
    view: str | None = None
    status: str | None = None
    supported: bool | None = None
    proxy_note: str | None = None
    error: str | None = None
    subject: str | None = None
    count: int = 0
    findings: list[AgentMemoryFinding] = Field(default_factory=list)
    evidence_refs: list[AgentMemoryEvidenceRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = (
    "AgentMemoryEvidenceFilters",
    "AgentMemoryEvidenceRef",
    "AgentMemoryFinding",
    "ControlRoomAgentMemoryResponse",
)
