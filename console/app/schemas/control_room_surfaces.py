from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EXPERIENCE_SCHEMA_VERSION = "control-room-experience/v1"
DIAGNOSTICS_SCHEMA_VERSION = "control-room-diagnostics/v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SurfaceScope(_StrictModel):
    tenant_id: str
    workspace_id: str


class ExperienceMetric(_StrictModel):
    name: str
    kind: Literal[
        "count",
        "rate",
        "percentage",
        "average",
        "division",
        "amount",
        "scalar",
    ]
    value: float
    unit: str | None = None


class ExperienceDecision(_StrictModel):
    reference: int
    status: Literal["decision_created", "approved", "resolved"]


class ExperienceCta(_StrictModel):
    label: str
    href: str


class ExperienceFact(_StrictModel):
    kind: Literal["anomaly", "signal", "alert", "kpi"]
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    observed_at: datetime
    stale: bool = False
    entity_label: str | None = None
    metric: ExperienceMetric | None = None
    decision: ExperienceDecision | None = None
    cta: ExperienceCta | None = None


class ExperienceSection(_StrictModel):
    title: str
    domain: str | None = None
    facts: list[ExperienceFact] = Field(default_factory=list)


class ControlRoomExperienceResponse(_StrictModel):
    schema_version: Literal["control-room-experience/v1"]
    generated_at: datetime
    scope: SurfaceScope
    sections: list[ExperienceSection] = Field(default_factory=list)


class DiagnosticSource(_StrictModel):
    cartridge: str
    dataset: str
    module: str | None = None
    domain: str | None = None
    status: str
    data_readiness: str | None = None
    count: int = 0
    operationally_ready: bool = False
    checked_at: datetime | None = None
    reason: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class DiagnosticItem(_StrictModel):
    kind: str
    title: str
    cartridge: str | None = None
    dataset: str | None = None
    module: str | None = None
    domain: str | None = None
    status: str | None = None
    data_status: str | None = None
    readiness_status: str | None = None
    source_status: str | None = None
    observed_at: datetime | None = None
    error: str | None = None


class DiagnosticInstallation(_StrictModel):
    cartridge_id: str
    status: str
    current_step: str | None = None
    label: str | None = None
    category: str | None = None
    ready_at: datetime | None = None
    error: str | None = None


class ControlRoomDiagnosticsResponse(_StrictModel):
    schema_version: Literal["control-room-diagnostics/v1"]
    generated_at: datetime
    scope: SurfaceScope
    sources: list[DiagnosticSource] = Field(default_factory=list)
    diagnostic_items: list[DiagnosticItem] = Field(default_factory=list)
    installations: list[DiagnosticInstallation] = Field(default_factory=list)


__all__ = (
    "DIAGNOSTICS_SCHEMA_VERSION",
    "EXPERIENCE_SCHEMA_VERSION",
    "ControlRoomDiagnosticsResponse",
    "ControlRoomExperienceResponse",
    "DiagnosticInstallation",
    "DiagnosticItem",
    "DiagnosticSource",
    "ExperienceCta",
    "ExperienceDecision",
    "ExperienceFact",
    "ExperienceMetric",
    "ExperienceSection",
    "SurfaceScope",
)
