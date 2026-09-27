from __future__ import annotations

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.control_room.business_action_binding import (
    normalize_action_idempotency_key,
)

EXCEPTION_APPROVED_MESSAGE = (
    "Hallazgo archivado como excepción aprobada; puedes reabrirlo desde "
    "Excepciones aprobadas."
)
EXCEPTION_REOPENED_MESSAGE = "Hallazgo reabierto; vuelve a la lista de hallazgos."
PROPOSAL_CREATED_MESSAGE = (
    "Propuesta de decisión creada; pasa al Consejo de Acciones para aprobación."
)
PROPOSAL_EXISTS_MESSAGE = (
    "La propuesta de decisión ya estaba registrada en el Consejo de Acciones."
)
_BIDI_CONTROL_RANGES = ((0x202A, 0x202E), (0x2066, 0x2069))
_HANDLE_PATTERN = r"^[a-f0-9]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def normalize_reason(value: object, *, minimum: int, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError("reason must be a string")
    if any(
        unicodedata.category(character) == "Cc"
        or any(low <= ord(character) <= high for low, high in _BIDI_CONTROL_RANGES)
        for character in value
    ):
        raise ValueError("reason contains control characters")
    normalized = value.strip()
    if len(normalized) < minimum:
        raise ValueError(f"reason must have at least {minimum} characters")
    if len(normalized) > maximum:
        raise ValueError(f"reason exceeds {maximum} characters")
    return normalized


class DirectActionHandleRequest(_StrictModel):
    action_handle: str = Field(pattern=_HANDLE_PATTERN)


class _IdempotentRequest(DirectActionHandleRequest):
    idempotency_key: str | None = None

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> object:
        return normalize_action_idempotency_key(value)


class ExceptionApprovalRequest(_IdempotentRequest):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str:
        return normalize_reason(value, minimum=10)


class ExceptionReopenRequest(_IdempotentRequest):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str:
        return normalize_reason(value, minimum=3)


class DecisionProposalRequest(_IdempotentRequest):
    pass


class ExceptionApprovalResponse(_StrictModel):
    action_handle: str = Field(pattern=_HANDLE_PATTERN)
    status: Literal["exception_approved"] = "exception_approved"
    reversible: Literal[True] = True
    message: Literal[EXCEPTION_APPROVED_MESSAGE] = EXCEPTION_APPROVED_MESSAGE


class ExceptionReopenResponse(_StrictModel):
    action_handle: str = Field(pattern=_HANDLE_PATTERN)
    status: Literal["exception_reopened"] = "exception_reopened"
    message: Literal[EXCEPTION_REOPENED_MESSAGE] = EXCEPTION_REOPENED_MESSAGE


class DecisionProposalResponse(_StrictModel):
    action_handle: str = Field(pattern=_HANDLE_PATTERN)
    status: Literal["proposal_created", "proposal_exists"]
    decision_id: int = Field(gt=0)
    href: str = Field(pattern=r"^/decisions\?tab=consejo&propuesta=[1-9][0-9]{0,18}$")
    message: Literal[PROPOSAL_CREATED_MESSAGE, PROPOSAL_EXISTS_MESSAGE]


class StudioTargetResponse(_StrictModel):
    action_handle: str = Field(pattern=_HANDLE_PATTERN)
    href: str = Field(pattern=r"^/studio\?cartridge=[a-z0-9_]{1,120}&tab=capas$")


__all__ = (
    "DecisionProposalRequest",
    "DecisionProposalResponse",
    "DirectActionHandleRequest",
    "EXCEPTION_APPROVED_MESSAGE",
    "EXCEPTION_REOPENED_MESSAGE",
    "ExceptionApprovalRequest",
    "ExceptionApprovalResponse",
    "ExceptionReopenRequest",
    "ExceptionReopenResponse",
    "PROPOSAL_CREATED_MESSAGE",
    "PROPOSAL_EXISTS_MESSAGE",
    "StudioTargetResponse",
    "normalize_reason",
)
