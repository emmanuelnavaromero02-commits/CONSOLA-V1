from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.control_room_surfaces import (
    ExperienceDecision,
    ExperienceMetric,
    SurfaceScope,
)
from app.services.control_room.business_action_binding import (
    valid_action_item_id,
    valid_action_template_binding,
    valid_action_template_id,
    valid_preview_action_endpoint,
)


EXPERIENCE_ACTIONS_SCHEMA_VERSION = "control-room-experience/v2"
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


class ExperienceActionSource(_StrictModel):
    dataset: str = Field(min_length=1, max_length=240)
    system: str = Field(min_length=1, max_length=120)
    cartridge: str = Field(min_length=1, max_length=120)


class ExperienceActionProvenance(_StrictModel):
    producer: Literal["control_room_action_policy"]
    evidence: Literal["verified_business_observation"]


class ExperienceActionBinding(_StrictModel):
    version: Literal["control-room-action-binding/v1"]
    policy_version: Literal["control-room-business-v2"]
    item_id: str = Field(min_length=1, max_length=240)
    template_id: str = Field(min_length=1, max_length=120)
    tenant_id: str = Field(min_length=1, max_length=240)
    workspace_id: str = Field(min_length=1, max_length=240)
    observation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: ExperienceActionSource
    provenance: ExperienceActionProvenance
    binding_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExperienceAction(_StrictModel):
    item_id: str = Field(min_length=1, max_length=240)
    template_id: str = Field(min_length=1, max_length=120)
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
    endpoint: str = Field(min_length=1, max_length=320)
    binding: ExperienceActionBinding

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if not valid_action_item_id(self.item_id):
            raise ValueError("invalid action item id")
        if not valid_action_template_id(self.template_id):
            raise ValueError("invalid action template id")
        if not valid_action_template_binding(
            self.template_id,
            label=self.label,
            requires_approval=self.requires_approval,
        ):
            raise ValueError("invalid action template binding")
        if not valid_preview_action_endpoint(self.endpoint, item_id=self.item_id):
            raise ValueError("invalid action endpoint")
        if (
            self.binding.item_id != self.item_id
            or self.binding.template_id != self.template_id
        ):
            raise ValueError("action binding does not match the action")
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
    actions: list[ExperienceAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_item_binding(self) -> Self:
        if len({action.item_id for action in self.actions}) > 1:
            raise ValueError("actions belong to different items")
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
    scope: SurfaceScope
    sections: list[ExperienceSectionV2] = Field(default_factory=list)


__all__ = (
    "EXPERIENCE_ACTIONS_SCHEMA_VERSION",
    "ControlRoomExperienceV2Response",
    "ExperienceAction",
    "ExperienceActionBinding",
    "ExperienceActionPrerequisite",
    "ExperienceActionProvenance",
    "ExperienceActionSource",
    "ExperienceFactV2",
    "ExperienceSectionV2",
)
