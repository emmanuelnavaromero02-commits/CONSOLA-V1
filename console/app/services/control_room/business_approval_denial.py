from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import HTTPException

from app.schemas.control_room_council import (
    CHECKER_INFLUENCED_REASON,
    NEEDS_OTHER_APPROVER_REASON,
)
from app.services.control_room.business_council_actors import CouncilMaker


DENIED_AUDIT_ACTION = "control_room.approve.denied"
SELF_APPROVAL = "self_approval"
SELF_DISCARD = "self_discard"
IMPLICIT_SELF_APPROVAL = "implicit_self_approval"
NO_AUTHOR = "decision_without_author"
APPROVER_PERMISSION = "approver_permission_required"
CHECKER_INFLUENCED = "checker_influenced_proposal"
DENIAL_MESSAGES = {
    SELF_APPROVAL: (
        "Quien creó la decisión no puede aprobarla. " + NEEDS_OTHER_APPROVER_REASON
    ),
    SELF_DISCARD: (
        "Quien creó la propuesta no puede descartarla. " + NEEDS_OTHER_APPROVER_REASON
    ),
    IMPLICIT_SELF_APPROVAL: (
        "No se puede crear y aprobar una decisión en un solo paso. "
        + NEEDS_OTHER_APPROVER_REASON
    ),
    NO_AUTHOR: "La decisión no tiene un autor vigente; ciérrala desde el Registro.",
    APPROVER_PERMISSION: (
        "Aprobar decisiones del Control Room requiere el permiso de aprobación "
        "y ejecución vigente en este espacio de trabajo."
    ),
    CHECKER_INFLUENCED: CHECKER_INFLUENCED_REASON,
}
AuditRecorder = Callable[..., Awaitable[None]]


class ItemApprovalDenied(HTTPException):
    def __init__(
        self,
        reason: str,
        *,
        decision_id: int | None = None,
        maker: CouncilMaker | None = None,
        item_id: str | None = None,
    ) -> None:
        super().__init__(403, {"code": reason, "message": DENIAL_MESSAGES[reason]})
        self.reason = reason
        self.decision_id = decision_id
        self.maker = maker
        self.item_id = item_id


async def record_item_approval_denial(
    record_audit_event: AuditRecorder,
    *,
    user: Mapping[str, Any],
    item_id: str,
    denial: ItemApprovalDenied,
    requested_decision_id: int | None,
    ip: str | None = None,
    user_agent: str | None = None,
    action: str = DENIED_AUDIT_ACTION,
) -> None:
    await record_audit_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action=action,
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="denied",
        metadata={
            "reason": denial.reason,
            "decision_id": denial.decision_id or requested_decision_id,
            "maker": denial.maker.label if denial.maker is not None else None,
        },
        critical=True,
    )


__all__ = (
    "APPROVER_PERMISSION",
    "CHECKER_INFLUENCED",
    "DENIAL_MESSAGES",
    "DENIED_AUDIT_ACTION",
    "IMPLICIT_SELF_APPROVAL",
    "NO_AUTHOR",
    "SELF_APPROVAL",
    "SELF_DISCARD",
    "ItemApprovalDenied",
    "record_item_approval_denial",
)
