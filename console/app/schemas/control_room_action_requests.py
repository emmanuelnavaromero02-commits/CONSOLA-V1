from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.control_room.business_action_binding import (
    normalize_action_idempotency_key,
)


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ControlRoomActionRequest(_StrictRequest):
    template_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    binding_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class ControlRoomActionHandleRequest(_StrictRequest):
    action_handle: str = Field(pattern=r"^[a-f0-9]{64}$")


class ControlRoomExecuteRequest(ControlRoomActionRequest):
    confirm_execute: bool = False
    confirmation: bool | None = None
    idempotency_key: str | None = None

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def normalize_idempotency_key(cls, value: object) -> object:
        return normalize_action_idempotency_key(value)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "ControlRoomExecuteRequest":
        if (
            self.confirmation is not None
            and self.confirm_execute
            and self.confirmation is not self.confirm_execute
        ):
            raise ValueError("conflicting execution confirmation")
        return self

    @property
    def confirmed(self) -> bool:
        return self.confirm_execute or self.confirmation is True


__all__ = (
    "ControlRoomActionRequest",
    "ControlRoomActionHandleRequest",
    "ControlRoomExecuteRequest",
)
