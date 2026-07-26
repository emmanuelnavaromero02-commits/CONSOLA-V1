from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ControlRoomApprovalRequest(_StrictRequest):
    decision_id: StrictInt = Field(gt=0, le=9_223_372_036_854_775_807)


class ControlRoomThresholdRequest(BaseModel):
    """Preserve the legacy threshold shape while validating its boolean switch."""

    model_config = ConfigDict(extra="allow", strict=True)

    enabled: StrictBool = True


__all__ = (
    "ControlRoomApprovalRequest",
    "ControlRoomThresholdRequest",
)
