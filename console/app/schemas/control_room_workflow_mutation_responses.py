from __future__ import annotations

from typing import cast

from app.schemas.control_room_mutation_common import (
    MutationControl,
    MutationItemSummary,
    MutationLesson,
    MutationLessonApplication,
    MutationOutcome,
)
from app.schemas.control_room_public_projection import PublicProjectionModel
from pydantic import Field


class ControlRoomStepMutationResponse(PublicProjectionModel):
    recorded: bool = False
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomCreateLessonMutationResponse(PublicProjectionModel):
    created: bool = False
    lesson: MutationLesson = Field(default_factory=MutationLesson)
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomOutcomeMutationResponse(PublicProjectionModel):
    recorded: bool = False
    outcome: MutationOutcome = Field(default_factory=MutationOutcome)
    lesson_recorded: bool = False
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomApplyLessonMutationResponse(PublicProjectionModel):
    applied: bool = False
    lesson: MutationLesson = Field(default_factory=MutationLesson)
    lesson_application: MutationLessonApplication = Field(
        default_factory=MutationLessonApplication
    )
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


class ControlRoomControlMutationResponse(PublicProjectionModel):
    updated: bool = False
    control: MutationControl = Field(default_factory=MutationControl)
    item: MutationItemSummary = Field(default_factory=MutationItemSummary)


def project_step_mutation_response(value: object) -> ControlRoomStepMutationResponse:
    return cast(
        ControlRoomStepMutationResponse, ControlRoomStepMutationResponse.project(value)
    )


def project_create_lesson_mutation_response(
    value: object,
) -> ControlRoomCreateLessonMutationResponse:
    return cast(
        ControlRoomCreateLessonMutationResponse,
        ControlRoomCreateLessonMutationResponse.project(value),
    )


def project_outcome_mutation_response(
    value: object,
) -> ControlRoomOutcomeMutationResponse:
    return cast(
        ControlRoomOutcomeMutationResponse,
        ControlRoomOutcomeMutationResponse.project(value),
    )


def project_apply_lesson_mutation_response(
    value: object,
) -> ControlRoomApplyLessonMutationResponse:
    return cast(
        ControlRoomApplyLessonMutationResponse,
        ControlRoomApplyLessonMutationResponse.project(value),
    )


def project_control_mutation_response(
    value: object,
) -> ControlRoomControlMutationResponse:
    return cast(
        ControlRoomControlMutationResponse,
        ControlRoomControlMutationResponse.project(value),
    )


__all__ = (
    "ControlRoomApplyLessonMutationResponse",
    "ControlRoomControlMutationResponse",
    "ControlRoomCreateLessonMutationResponse",
    "ControlRoomOutcomeMutationResponse",
    "ControlRoomStepMutationResponse",
    "project_apply_lesson_mutation_response",
    "project_control_mutation_response",
    "project_create_lesson_mutation_response",
    "project_outcome_mutation_response",
    "project_step_mutation_response",
)
