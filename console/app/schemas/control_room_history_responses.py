from __future__ import annotations

from pydantic import Field

from app.schemas.control_room_business_responses import PublicImpactDriver
from app.schemas.control_room_public_projection import PublicProjectionModel


class ControlRoomLegacyImpactResponse(PublicProjectionModel):
    item_id: str = ""
    status: str | None = None
    estimate: float | int | None = None
    currency: str | None = None
    confidence: float | int | None = None
    priority_score: float | int | None = None
    explanation: str | None = None
    drivers: list[PublicImpactDriver] = Field(default_factory=list)


class PublicActivity(PublicProjectionModel):
    label: str | None = None
    status: str | None = None
    at: str | None = None


class PublicActivityCounts(PublicProjectionModel):
    events: int = 0
    executions: int = 0
    decision_actions: int = 0
    action_runs: int = 0
    outcomes: int = 0
    total: int = 0


class ControlRoomLegacyActivityResponse(PublicProjectionModel):
    item_id: str = ""
    activity: list[PublicActivity] = Field(default_factory=list)
    counts: PublicActivityCounts = Field(default_factory=PublicActivityCounts)


class ControlRoomLegacyActionRun(PublicProjectionModel):
    mode: str | None = None
    status: str = ""
    risk_level: str | None = None
    requires_approval: bool | None = None
    approval_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


class ControlRoomLegacyActionRunsResponse(PublicProjectionModel):
    item_id: str = ""
    action_runs: list[ControlRoomLegacyActionRun] = Field(default_factory=list)


class ControlRoomLegacyOutcome(PublicProjectionModel):
    action_taken: str | None = None
    predicted_value: float | int | None = None
    actual_value: float | int | None = None
    prediction_error: float | int | None = None
    outcome_summary: str | None = None
    learned_rule: str | None = None
    created_at: str | None = None


class ControlRoomLegacyOutcomesResponse(PublicProjectionModel):
    item_id: str = ""
    outcomes: list[ControlRoomLegacyOutcome] = Field(default_factory=list)


__all__ = tuple(name for name in globals() if name.startswith("ControlRoom"))
