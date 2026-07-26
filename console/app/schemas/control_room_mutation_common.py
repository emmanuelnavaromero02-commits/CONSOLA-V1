from __future__ import annotations

from app.schemas.control_room_public_projection import PublicProjectionModel


class MutationItemSummary(PublicProjectionModel):
    """Small business-facing item state shared by mutation responses."""

    kind: str | None = None
    title: str | None = None
    severity: str | None = None
    status: str | None = None
    lesson_count: int | None = None


class MutationLesson(PublicProjectionModel):
    """Reusable lesson handle and visible business rule."""

    anomaly_type: str | None = None
    rule: str | None = None
    confidence: float | int | None = None
    created_at: str | None = None


class MutationLessonApplication(PublicProjectionModel):
    rule: str | None = None
    note: str | None = None
    applied_at: str | None = None


class MutationOutcome(PublicProjectionModel):
    action_taken: str | None = None
    predicted_value: float | int | None = None
    actual_value: float | int | None = None
    prediction_error: float | int | None = None
    outcome_summary: str | None = None
    learned_rule: str | None = None
    created_at: str | None = None


class MutationControl(PublicProjectionModel):
    status: str | None = None
    due_at: str | None = None
    note: str | None = None
    updated_at: str | None = None


class MutationThreshold(PublicProjectionModel):
    anomaly_type: str | None = None
    metric: str | None = None
    warning_value: float | int | None = None
    critical_value: float | int | None = None
    currency: str | None = None
    enabled: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


__all__ = (
    "MutationControl",
    "MutationItemSummary",
    "MutationLesson",
    "MutationLessonApplication",
    "MutationOutcome",
    "MutationThreshold",
)
