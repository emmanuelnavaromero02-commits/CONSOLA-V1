from __future__ import annotations

from typing import cast

from app.schemas.control_room_mutation_common import MutationItemSummary
from app.schemas.control_room_public_projection import PublicProjectionModel
from pydantic import Field


class MutationAlertDelivery(PublicProjectionModel):
    status: str | None = None
    enabled: bool | None = None
    reason: str | None = None


class MutationAlert(PublicProjectionModel):
    alert_type: str | None = None
    title: str | None = None
    message: str | None = None
    severity: str | None = None
    status: str | None = None
    domain: str | None = None
    priority_score: float | int | None = None
    occurrence_count: int | None = None
    advisory: bool | None = None
    push_ready: bool | None = None
    note: str | None = None
    reason: str | None = None
    acknowledged_at: str | None = None
    assigned_at: str | None = None
    snoozed_until: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    delivery: MutationAlertDelivery = Field(default_factory=MutationAlertDelivery)


class ControlRoomAlertMutationResponse(PublicProjectionModel):
    ok: bool = False
    alert: MutationAlert | None = None
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


def project_alert_mutation_response(value: object) -> ControlRoomAlertMutationResponse:
    return cast(
        ControlRoomAlertMutationResponse,
        ControlRoomAlertMutationResponse.project(value),
    )


__all__ = (
    "ControlRoomAlertMutationResponse",
    "project_alert_mutation_response",
)
