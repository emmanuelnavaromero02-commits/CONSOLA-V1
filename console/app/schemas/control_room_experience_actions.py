from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.control_room_surfaces import (
    ExperienceDecision,
    ExperienceMetric,
)

EXPERIENCE_ACTIONS_SCHEMA_VERSION = "control-room-experience/v2"
EXPERIENCE_ACTION_PREVIEW_ENDPOINT = "/api/control-room/actions/preview"
PrerequisiteCode = Literal[
    "business_eligible",
    "evidence",
    "scope",
    "template",
    "permission",
    "non_terminal",
    "freshness",
    "source_binding",
]
DisabledReason = Literal[
    "Actualiza los datos antes de continuar.",
    "Completa los datos requeridos antes de continuar.",
]
_PREREQUISITE_CODES = {
    "business_eligible",
    "evidence",
    "scope",
    "template",
    "permission",
    "non_terminal",
    "freshness",
    "source_binding",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExperienceActionPrerequisite(_StrictModel):
    code: PrerequisiteCode
    satisfied: bool


class ExperienceAction(_StrictModel):
    action_handle: str = Field(pattern=r"^[a-f0-9]{64}$")
    label: str = Field(min_length=1, max_length=120)
    operation: Literal["preview"]
    enabled: bool
    requires_approval: bool
    prerequisites: list[ExperienceActionPrerequisite] = Field(
        min_length=8,
        max_length=8,
    )
    disabled_reason: DisabledReason | None = None
    method: Literal["POST"]
    endpoint: Literal["/api/control-room/actions/preview"]

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        codes = [prerequisite.code for prerequisite in self.prerequisites]
        if len(set(codes)) != len(codes) or set(codes) != _PREREQUISITE_CODES:
            raise ValueError("invalid action prerequisites")
        all_satisfied = all(value.satisfied for value in self.prerequisites)
        if self.enabled != all_satisfied:
            raise ValueError("action enabled state does not match prerequisites")
        if self.enabled != (self.disabled_reason is None):
            raise ValueError("action disabled reason does not match enabled state")
        return self


class ExperienceFactV2(_StrictModel):
    kind: Literal["anomaly", "signal", "alert", "kpi"]
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    observed_at: datetime
    stale: bool = False
    entity_label: str | None = None
    metric: ExperienceMetric | None = None
    decision: ExperienceDecision | None = None
    actions: list[ExperienceAction] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_item_binding(self) -> Self:
        handles = [action.action_handle for action in self.actions]
        if len(set(handles)) != len(handles):
            raise ValueError("duplicate action handles")
        return self


class ExperienceSectionV2(_StrictModel):
    id: str
    cartridge_id: str
    module_id: str
    title: str
    domain: str
    facts: list[ExperienceFactV2]


class ControlRoomExperienceV2Response(_StrictModel):
    schema_version: Literal["control-room-experience/v2"]
    generated_at: datetime
    sections: list[ExperienceSectionV2] = Field(default_factory=list)


__all__ = (
    "EXPERIENCE_ACTIONS_SCHEMA_VERSION",
    "EXPERIENCE_ACTION_PREVIEW_ENDPOINT",
    "ControlRoomExperienceV2Response",
    "ExperienceAction",
    "ExperienceActionPrerequisite",
    "ExperienceFactV2",
    "ExperienceSectionV2",
)
