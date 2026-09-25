from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.control_room_diagnostic_enums import (
    DiagnosticInstallationStatus,
    DiagnosticItemKind,
    DiagnosticItemStatus,
    DiagnosticReadinessStatus,
    DiagnosticSourceStatus,
)


EXPERIENCE_SCHEMA_VERSION = "control-room-experience/v1"
DIAGNOSTICS_SCHEMA_VERSION = "control-room-diagnostics/v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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
    status: Literal["decision_created", "approved", "resolved"]


class ExperienceFact(_StrictModel):

    kind: Literal["anomaly", "signal", "alert", "kpi"]
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    observed_at: datetime
    stale: bool = False
    entity_label: str | None = None
    metric: ExperienceMetric | None = None
    decision: ExperienceDecision | None = None


class ExperienceSection(_StrictModel):
    title: str
    domain: str
    facts: list[ExperienceFact]


class ControlRoomExperienceResponse(_StrictModel):
    schema_version: Literal["control-room-experience/v1"]
    generated_at: datetime
    sections: list[ExperienceSection] = Field(default_factory=list)


class DiagnosticSource(_StrictModel):
    domain: str | None = None
    status: DiagnosticSourceStatus
    data_readiness: DiagnosticReadinessStatus | None = None
    count: int | None = Field(default=None, ge=0)
    operationally_ready: bool = False
    checked_at: datetime | None = None
    reason: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class DiagnosticItem(_StrictModel):
    kind: DiagnosticItemKind
    title: str
    domain: str | None = None
    status: DiagnosticItemStatus | None = None
    data_status: DiagnosticReadinessStatus | None = None
    readiness_status: DiagnosticReadinessStatus | None = None
    source_status: DiagnosticSourceStatus | None = None
    observed_at: datetime | None = None
    error: str | None = None


class DiagnosticInstallation(_StrictModel):
    status: DiagnosticInstallationStatus
    label: str | None = None
    category: str | None = None
    ready_at: datetime | None = None
    error: str | None = None


class ControlRoomDiagnosticsResponse(_StrictModel):
    schema_version: Literal["control-room-diagnostics/v1"]
    generated_at: datetime
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
    "ExperienceDecision",
    "ExperienceFact",
    "ExperienceMetric",
    "ExperienceSection",
)
