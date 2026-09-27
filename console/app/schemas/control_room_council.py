from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.control_room_direct_actions import normalize_reason
from app.schemas.control_room_experience_actions import ExperienceNarrative
from app.services.control_room.business_action_binding import (
    normalize_action_idempotency_key,
)

COUNCIL_SCHEMA_VERSION = "control-room-council/v1"
MAX_COUNCIL_PROPOSALS = 50
MAX_COUNCIL_EVIDENCE = 8
NEEDS_OTHER_APPROVER_REASON = "Requiere la aprobación de otra persona del equipo."
EXPIRED_REASON = "La propuesta venció; su autor puede renovarla."
NO_FOLLOWUP_REASON = (
    "La tarea de seguimiento no está preparada; su autor puede renovarla."
)
SOURCE_CHANGED_REASON = "Los datos de origen cambiaron desde que se preparó la propuesta."
APPROVED_MESSAGE = (
    "Decisión aprobada y tarea de seguimiento interna registrada. "
    "No se modificó ningún sistema externo (ERP)."
)
DISCARDED_MESSAGE = "Propuesta descartada; el motivo quedó registrado."
RENEWED_MESSAGE = (
    "Propuesta renovada; espera la aprobación de otra persona del equipo."
)
NO_ESTIMATE_LABEL = "Sin estimación"
_HANDLE = r"^[a-f0-9]{64}$"

CouncilState = Literal[
    "pending_approval",
    "needs_other_approver",
    "expired",
    "no_followup",
    "source_changed",
    "completed",
]
CouncilDisabledReason = Literal[
    "Requiere la aprobación de otra persona del equipo.",
    "La propuesta venció; su autor puede renovarla.",
    "La tarea de seguimiento no está preparada; su autor puede renovarla.",
    "Los datos de origen cambiaron desde que se preparó la propuesta.",
]
EvidenceLabel = Literal["Entidad", "Indicador", "Fecha del dato", "Severidad"]
_Copy = Annotated[str, Field(min_length=1, max_length=240)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CouncilImpact(_StrictModel):
    kind: Literal["money", "time", "none"]
    value: float | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    unit: Literal["horas"] | None = None
    basis: Literal["persisted", "rule", "observed"] | None = None
    formula: str | None = Field(default=None, min_length=1, max_length=240)
    label: _Copy

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "none":
            if any(
                value is not None
                for value in (self.value, self.currency, self.unit, self.basis)
            ) or self.label != NO_ESTIMATE_LABEL:
                raise ValueError("impact without estimate carries no figures")
            return self
        if self.value is None or self.basis is None or self.formula is None:
            raise ValueError("impact estimates always carry value, basis and formula")
        if (self.kind == "money") != (self.currency is not None):
            raise ValueError("only money impacts carry a currency")
        if (self.kind == "time") != (self.unit is not None):
            raise ValueError("only time impacts carry a unit")
        return self


class CouncilEvidence(_StrictModel):
    label: EvidenceLabel
    value: _Copy


class CouncilProposal(_StrictModel):
    proposal_id: str = Field(pattern=_HANDLE)
    origin: Literal["system", "person"]
    authored_by_you: bool
    decision_id: int | None = Field(default=None, gt=0)
    title: _Copy
    section_title: str | None = Field(default=None, min_length=1, max_length=240)
    severity: Literal["critical", "high", "medium", "low"]
    observed_at: datetime | None = None
    created_at: datetime | None = None
    commitment_date: date | None = None
    state: CouncilState
    impact: CouncilImpact
    evidence: list[CouncilEvidence] = Field(
        default_factory=list, max_length=MAX_COUNCIL_EVIDENCE
    )
    narrative: ExperienceNarrative | None = None
    can_approve: bool
    can_discard: bool
    can_renew: bool
    disabled_reason: CouncilDisabledReason | None = None

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if self.can_approve and self.disabled_reason is not None:
            raise ValueError("an approvable proposal has no disabled reason")
        if self.origin == "system" and (
            self.decision_id is not None or self.can_renew or self.authored_by_you
        ):
            raise ValueError("system suggestions have no decision or author")
        if self.origin == "person" and self.decision_id is None:
            raise ValueError("person proposals are backed by a decision")
        if self.state == "completed" and (
            self.can_approve or self.can_discard or self.can_renew
        ):
            raise ValueError("completed proposals are read-only")
        return self


class ActionCouncilResponse(_StrictModel):
    schema_version: Literal["control-room-council/v1"] = COUNCIL_SCHEMA_VERSION
    generated_at: datetime
    proposals: list[CouncilProposal] = Field(
        default_factory=list, max_length=MAX_COUNCIL_PROPOSALS
    )


class _IdempotentCommand(_StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> object:
        return normalize_action_idempotency_key(value)


class CouncilApproveRequest(_IdempotentCommand):
    confirm: Literal[True]


class CouncilDiscardRequest(_IdempotentCommand):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str:
        return normalize_reason(value, minimum=10)


class CouncilRenewRequest(_StrictModel):
    confirm: Literal[True]


class CouncilApproveResponse(_StrictModel):
    status: Literal["approved_with_followup"] = "approved_with_followup"
    decision_id: int = Field(gt=0)
    followup_created: Literal[True] = True
    message: Literal[APPROVED_MESSAGE] = APPROVED_MESSAGE


class CouncilDiscardResponse(_StrictModel):
    status: Literal["discarded"] = "discarded"
    decision_id: int | None = Field(default=None, gt=0)
    message: Literal[DISCARDED_MESSAGE] = DISCARDED_MESSAGE


class CouncilRenewResponse(_StrictModel):
    status: Literal["renewed"] = "renewed"
    decision_id: int = Field(gt=0)
    message: Literal[RENEWED_MESSAGE] = RENEWED_MESSAGE


__all__ = (
    "APPROVED_MESSAGE",
    "COUNCIL_SCHEMA_VERSION",
    "DISCARDED_MESSAGE",
    "EXPIRED_REASON",
    "MAX_COUNCIL_EVIDENCE",
    "MAX_COUNCIL_PROPOSALS",
    "NEEDS_OTHER_APPROVER_REASON",
    "NO_ESTIMATE_LABEL",
    "NO_FOLLOWUP_REASON",
    "RENEWED_MESSAGE",
    "SOURCE_CHANGED_REASON",
    "ActionCouncilResponse",
    "CouncilApproveRequest",
    "CouncilApproveResponse",
    "CouncilDiscardRequest",
    "CouncilDiscardResponse",
    "CouncilEvidence",
    "CouncilImpact",
    "CouncilProposal",
    "CouncilRenewRequest",
    "CouncilRenewResponse",
)
