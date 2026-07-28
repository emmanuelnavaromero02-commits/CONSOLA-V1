from __future__ import annotations

from typing import cast

from app.schemas.control_room_mutation_common import (
    MutationItemSummary,
    MutationThreshold,
)
from app.schemas.control_room_public_projection import PublicProjectionModel
from pydantic import Field


class ControlRoomDecisionMutationResponse(PublicProjectionModel):
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomOptionMutationResponse(PublicProjectionModel):
    selected: bool = False
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomApprovalMutationResponse(PublicProjectionModel):
    approved: bool = False
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomDismissMutationResponse(PublicProjectionModel):
    dismissed: bool = False
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomReopenMutationResponse(PublicProjectionModel):
    reopened: bool = False
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomThresholdMutationResponse(PublicProjectionModel):
    threshold: MutationThreshold = Field(default_factory=MutationThreshold)


def project_decision_mutation_response(
    value: object,
) -> ControlRoomDecisionMutationResponse:
    return cast(
        ControlRoomDecisionMutationResponse,
        ControlRoomDecisionMutationResponse.project(value),
    )


def project_option_mutation_response(
    value: object,
) -> ControlRoomOptionMutationResponse:
    return cast(
        ControlRoomOptionMutationResponse,
        ControlRoomOptionMutationResponse.project(value),
    )


def project_approval_mutation_response(
    value: object,
) -> ControlRoomApprovalMutationResponse:
    return cast(
        ControlRoomApprovalMutationResponse,
        ControlRoomApprovalMutationResponse.project(value),
    )


def project_dismiss_mutation_response(
    value: object,
) -> ControlRoomDismissMutationResponse:
    return cast(
        ControlRoomDismissMutationResponse,
        ControlRoomDismissMutationResponse.project(value),
    )


def project_reopen_mutation_response(
    value: object,
) -> ControlRoomReopenMutationResponse:
    return cast(
        ControlRoomReopenMutationResponse,
        ControlRoomReopenMutationResponse.project(value),
    )


def project_threshold_mutation_response(
    value: object,
) -> ControlRoomThresholdMutationResponse:
    return cast(
        ControlRoomThresholdMutationResponse,
        ControlRoomThresholdMutationResponse.project(value),
    )


__all__ = (
    "ControlRoomApprovalMutationResponse",
    "ControlRoomDecisionMutationResponse",
    "ControlRoomDismissMutationResponse",
    "ControlRoomOptionMutationResponse",
    "ControlRoomReopenMutationResponse",
    "ControlRoomThresholdMutationResponse",
    "project_approval_mutation_response",
    "project_decision_mutation_response",
    "project_dismiss_mutation_response",
    "project_option_mutation_response",
    "project_reopen_mutation_response",
    "project_threshold_mutation_response",
)
