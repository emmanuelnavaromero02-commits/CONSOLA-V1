from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTROL_ROOM_FRESHNESS_SCHEMA_VERSION = "control-room-freshness/v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ControlRoomFreshnessResponse(_StrictModel):
    schema_version: Literal["control-room-freshness/v1"]
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    checked_at: datetime
    data_refreshed_at: datetime | None = None


CONTROL_ROOM_REFRESHED_MESSAGE = "Información actualizada desde las fuentes."


class ControlRoomRefreshResponse(_StrictModel):
    status: Literal["refreshed"] = "refreshed"
    refreshed_at: datetime
    message: Literal[CONTROL_ROOM_REFRESHED_MESSAGE] = CONTROL_ROOM_REFRESHED_MESSAGE


__all__ = (
    "CONTROL_ROOM_FRESHNESS_SCHEMA_VERSION",
    "CONTROL_ROOM_REFRESHED_MESSAGE",
    "ControlRoomFreshnessResponse",
    "ControlRoomRefreshResponse",
)
