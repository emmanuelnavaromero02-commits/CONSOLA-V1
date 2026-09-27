from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


RUN_ID_PATTERN = r"^[A-Za-z0-9_.:+-]{1,250}$"
CARTRIDGE_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,127}$"
DAG_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,127}$"
PLAN_DIGEST_PATTERN = r"^[a-f0-9]{64}$"
MAX_RECOVERY_RUNS = 200

RunId = Annotated[str, StringConstraints(pattern=RUN_ID_PATTERN)]
RecoveryClassification = Literal[
    "not_applicable",
    "too_recent",
    "live",
    "waiting_turn",
    "airflow_terminal",
    "missing_in_airflow",
    "stalled_queued_paused_dag",
    "stalled_queued_no_progress",
    "stalled_running_no_tasks",
    "unverifiable",
]
RecoveryAction = Literal["mark_failed", "sync_terminal", "none"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StuckRunRecoveryRequest(_StrictModel):
    apply: bool = False
    plan_digest: str | None = Field(default=None, pattern=PLAN_DIGEST_PATTERN)
    cartridge: str | None = Field(default=None, pattern=CARTRIDGE_PATTERN)
    dag_id: str | None = Field(default=None, pattern=DAG_ID_PATTERN)
    threshold_minutes: int = Field(default=15, ge=15, le=1440)
    exclude_run_ids: list[RunId] = Field(
        default_factory=list, max_length=MAX_RECOVERY_RUNS
    )

    @model_validator(mode="after")
    def _digest_only_with_apply(self) -> "StuckRunRecoveryRequest":
        if self.apply and not self.plan_digest:
            raise ValueError("plan_digest is required to apply a recovery plan")
        if not self.apply and self.plan_digest:
            raise ValueError("plan_digest is only accepted together with apply")
        return self


class StuckRunRecoveryCounts(_StrictModel):
    candidates: int = Field(ge=0)
    recoverable: int = Field(ge=0)
    recovered: int = Field(ge=0)
    synced_terminal: int = Field(ge=0)
    live: int = Field(ge=0)
    unverifiable: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    airflow_neutralized: int = Field(ge=0)
    airflow_neutralize_failed: int = Field(ge=0)


class StuckRunRecoveryRun(_StrictModel):
    run_id: str
    dag_id: str
    cartridge: str
    entity: str
    status_before: str
    status_after: str | None = None
    started_at: datetime | None = None
    age_minutes: int | None = Field(default=None, ge=0)
    created_today: bool
    classification: RecoveryClassification
    action: RecoveryAction
    neutralize_airflow: bool
    reason_es: str


class StuckRunRecoveryResponse(_StrictModel):
    schema_version: Literal["pipeline-recovery/v1"] = "pipeline-recovery/v1"
    mode: Literal["dry_run", "applied"]
    checked_at: datetime
    threshold_minutes: int = Field(ge=1)
    plan_digest: str = Field(pattern=PLAN_DIGEST_PATTERN)
    counts: StuckRunRecoveryCounts
    runs: list[StuckRunRecoveryRun] = Field(max_length=MAX_RECOVERY_RUNS)
    truncated: bool
    message_es: str


MAX_PROGRESS_RUNS = 20


class ExtractionProgressRun(_StrictModel):
    run_id: str
    entity: str
    phase: Literal["connecting", "extracting", "building", "ready"]
    phase_index: int = Field(ge=1, le=4)
    status: str
    terminal: bool
    outcome: Literal["success", "partial", "failed"] | None = None
    record_count: int | None = Field(default=None, ge=0)
    entities_done: int | None = Field(default=None, ge=0)
    entities_total: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=500)
    recovered: bool
    stalled: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExtractionProgressResponse(_StrictModel):
    schema_version: Literal["pipeline-extraction-progress/v1"] = (
        "pipeline-extraction-progress/v1"
    )
    checked_at: datetime
    runs: list[ExtractionProgressRun] = Field(max_length=MAX_PROGRESS_RUNS)


__all__ = (
    "CARTRIDGE_PATTERN",
    "ExtractionProgressResponse",
    "ExtractionProgressRun",
    "MAX_PROGRESS_RUNS",
    "DAG_ID_PATTERN",
    "MAX_RECOVERY_RUNS",
    "PLAN_DIGEST_PATTERN",
    "RUN_ID_PATTERN",
    "StuckRunRecoveryCounts",
    "StuckRunRecoveryRequest",
    "StuckRunRecoveryResponse",
    "StuckRunRecoveryRun",
)
