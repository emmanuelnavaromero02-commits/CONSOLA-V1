from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AUTOMATIONS_SCHEMA_VERSION = "pipeline-automations/v1"
MAX_AUTOMATIONS = 200

AutomationState = Literal["active", "paused_by_operator", "paused_manual", "unavailable"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AutomationRun(_StrictModel):
    status: str = Field(pattern=r"^[a-z_]{1,40}$")
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Automation(_StrictModel):
    dag_id: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,250}$")
    label: str = Field(min_length=1, max_length=160)
    cartridge_id: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,120}$")
    kind: Literal["manual", "scheduled"]
    schedule_description: str | None = Field(default=None, max_length=160)
    state: AutomationState
    state_note_es: str = Field(min_length=1, max_length=240)
    active_runs: int | None = Field(default=None, ge=0)
    active_runs_capped: bool = False
    last_run: AutomationRun | None = None


class AutomationsResponse(_StrictModel):
    schema_version: Literal["pipeline-automations/v1"] = AUTOMATIONS_SCHEMA_VERSION
    checked_at: datetime
    airflow_available: bool
    automations: list[Automation] = Field(default_factory=list, max_length=MAX_AUTOMATIONS)


__all__ = (
    "AUTOMATIONS_SCHEMA_VERSION",
    "MAX_AUTOMATIONS",
    "Automation",
    "AutomationRun",
    "AutomationState",
    "AutomationsResponse",
)
