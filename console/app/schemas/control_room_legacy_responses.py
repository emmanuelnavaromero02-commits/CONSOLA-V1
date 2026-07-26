from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


_FORBIDDEN_KEY_FRAGMENTS = (
    "actor",
    "adapter",
    "authority_audit",
    "binding",
    "connection",
    "dataset",
    "digest",
    "email",
    "idempotency",
    "input",
    "metadata",
    "output",
    "payload",
    "provenance",
    "receipt",
    "result",
    "side_effect",
    "template",
    "tenant",
    "workspace",
)
_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        "authorization",
        "action_kind",
        "action_type",
        "body",
        "decision_id",
        "dry_run_result",
        "error",
        "error_code",
        "error_message",
        "event_id",
        "execution_id",
        "execution_result",
        "headers",
        "input",
        "legacy_execution_id",
        "method",
        "metadata",
        "option_id",
        "output",
        "preview_available",
        "query",
        "raw_sql",
        "request",
        "reservation_id",
        "response",
        "result",
        "run_id",
        "secret",
        "signal_id",
        "source_id",
        "sql",
        "statement",
        "user_id",
    }
)
_FORBIDDEN_COMPACT_FRAGMENTS = tuple(
    fragment.replace("_", "") for fragment in _FORBIDDEN_KEY_FRAGMENTS
)
_FORBIDDEN_EXACT_COMPACT_KEYS = frozenset(
    key.replace("_", "") for key in _FORBIDDEN_EXACT_KEYS
)


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def public_projection_key_is_forbidden(value: object) -> bool:
    key = _normalized_key(value)
    compact = key.replace("_", "")
    if compact == "metadatareadiness":
        return False
    return (
        key in _FORBIDDEN_EXACT_KEYS
        or compact in _FORBIDDEN_EXACT_COMPACT_KEYS
        or any(fragment in compact for fragment in _FORBIDDEN_COMPACT_FRAGMENTS)
    )


def redact_public_control_room_projection(value: Any) -> Any:
    """Copy a legacy projection while dropping server authority recursively."""
    if isinstance(value, Mapping):
        return {
            str(key): redact_public_control_room_projection(item)
            for key, item in value.items()
            if not public_projection_key_is_forbidden(key)
        }
    if isinstance(value, (list, tuple)):
        return [redact_public_control_room_projection(item) for item in value]
    return value


class _RedactedProjection(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def redact_server_authority(cls, value: Any) -> Any:
        return redact_public_control_room_projection(value)


class ControlRoomLegacyDashboardResponse(_RedactedProjection):
    meta: dict[str, Any] = Field(default_factory=dict)
    period: str | None = None
    omega_steps: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    domains: list[dict[str, Any]] = Field(default_factory=list)
    cartridges: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)


class ControlRoomLegacyAlertsResponse(_RedactedProjection):
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None


class ControlRoomLegacyAnomaliesResponse(_RedactedProjection):
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)


class ControlRoomLegacyItemResponse(_RedactedProjection):
    id: str
    kind: str
    title: str | None = None
    severity: str | None = None
    status: str | None = None
    recommendation: str | None = None
    detected_at: str | None = None
    omega: dict[str, Any] = Field(default_factory=dict)


class ControlRoomLegacyImpactResponse(_RedactedProjection):
    item_id: str
    status: str | None = None
    estimate: float | int | None = None
    currency: str | None = None
    confidence: float | int | None = None
    priority_score: float | int | None = None
    explanation: str | None = None
    drivers: list[dict[str, Any]] = Field(default_factory=list)


class ControlRoomLegacyActivityResponse(_RedactedProjection):
    item_id: str
    activity: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)


class ControlRoomLegacyActionRun(_RedactedProjection):
    model_config = ConfigDict(extra="ignore")

    mode: str | None = None
    status: str
    risk_level: str | None = None
    requires_approval: bool | None = None
    approval_status: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None
    completed_at: Any | None = None


class ControlRoomLegacyActionRunsResponse(_RedactedProjection):
    item_id: str
    action_runs: list[ControlRoomLegacyActionRun] = Field(default_factory=list)


class ControlRoomLegacyOutcome(_RedactedProjection):
    model_config = ConfigDict(extra="ignore")

    action_taken: str | None = None
    predicted_value: float | int | None = None
    actual_value: float | int | None = None
    prediction_error: float | int | None = None
    outcome_summary: str | None = None
    learned_rule: str | None = None
    created_at: Any | None = None


class ControlRoomLegacyOutcomesResponse(_RedactedProjection):
    item_id: str
    outcomes: list[ControlRoomLegacyOutcome] = Field(default_factory=list)


class ControlRoomTalentKpisResponse(_RedactedProjection):
    generated_at: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)
    readiness: dict[str, Any] = Field(default_factory=dict)
    widgets: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    workforce_trends: dict[str, Any] | None = None


class ControlRoomTalentOverviewResponse(ControlRoomTalentKpisResponse):
    nine_box: dict[str, Any] = Field(default_factory=dict)
    anomalies: dict[str, Any] = Field(default_factory=dict)
    metadata_readiness: dict[str, Any] = Field(default_factory=dict)


class ControlRoomTalentNineBoxResponse(_RedactedProjection):
    generated_at: str | None = None
    status: str
    totals: dict[str, Any] = Field(default_factory=dict)
    cells: list[dict[str, Any]] = Field(default_factory=list)
    desempeno_disponible: dict[str, Any] | None = None
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    privacy: dict[str, Any] | None = None


class ControlRoomTalentRosterResponse(_RedactedProjection):
    generated_at: str | None = None
    box: dict[str, Any] = Field(default_factory=dict)
    status: str
    count: int = 0
    roster: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    privacy: dict[str, Any] | None = None


class ControlRoomTalentAnomaliesResponse(_RedactedProjection):
    generated_at: str | None = None
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[dict[str, Any]] = Field(default_factory=list)


class ControlRoomTalentMetadataReadinessResponse(_RedactedProjection):
    generated_at: str | None = None
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    live_preflight: dict[str, Any] | None = None


__all__ = (
    "ControlRoomLegacyActionRunsResponse",
    "ControlRoomLegacyActionRun",
    "ControlRoomLegacyActivityResponse",
    "ControlRoomLegacyAlertsResponse",
    "ControlRoomLegacyAnomaliesResponse",
    "ControlRoomLegacyDashboardResponse",
    "ControlRoomLegacyImpactResponse",
    "ControlRoomLegacyItemResponse",
    "ControlRoomLegacyOutcomesResponse",
    "ControlRoomLegacyOutcome",
    "ControlRoomTalentAnomaliesResponse",
    "ControlRoomTalentKpisResponse",
    "ControlRoomTalentMetadataReadinessResponse",
    "ControlRoomTalentNineBoxResponse",
    "ControlRoomTalentOverviewResponse",
    "ControlRoomTalentRosterResponse",
    "public_projection_key_is_forbidden",
    "redact_public_control_room_projection",
)
