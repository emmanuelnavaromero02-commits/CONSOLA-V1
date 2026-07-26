from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.control_room_surfaces import (
    ExperienceDecision,
    ExperienceMetric,
)

EXPERIENCE_ACTIONS_SCHEMA_VERSION = "control-room-experience/v2"
DisabledReason = Literal[
    "Actualiza los datos antes de continuar.",
    "Completa los datos requeridos antes de continuar.",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExperienceAction(_StrictModel):
    action_handle: str = Field(pattern=r"^[a-f0-9]{64}$")
    label: str = Field(min_length=1, max_length=120)
    enabled: bool
    requires_approval: bool
    disabled_reason: DisabledReason | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.enabled != (self.disabled_reason is None):
            raise ValueError("action disabled reason does not match enabled state")
        return self


class ExperienceActionPreviewResponse(_StrictModel):
    action_handle: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["preview"] = "preview"
    status: Literal["generated"] = "generated"
    message: Literal["Preview generado; no se ejecuto ningun cambio externo."] = (
        "Preview generado; no se ejecuto ningun cambio externo."
    )


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
    title: str
    facts: list[ExperienceFactV2]


class ControlRoomExperienceV2Response(_StrictModel):
    schema_version: Literal["control-room-experience/v2"]
    generated_at: datetime
    sections: list[ExperienceSectionV2] = Field(default_factory=list)


__all__ = (
    "EXPERIENCE_ACTIONS_SCHEMA_VERSION",
    "ControlRoomExperienceV2Response",
    "ExperienceAction",
    "ExperienceActionPreviewResponse",
    "ExperienceFactV2",
    "ExperienceSectionV2",
)
