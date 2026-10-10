from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import actor_id as optional_actor_id
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
)
from app.services.control_room.business_approval_denial import (
    APPROVER_PERMISSION,
    CHECKER_INFLUENCED,
    DENIAL_MESSAGES,
    DENIED_AUDIT_ACTION,
    IMPLICIT_SELF_APPROVAL,
    NO_AUTHOR,
    SELF_APPROVAL,
    SELF_DISCARD,
    ItemApprovalDenied,
    record_item_approval_denial,
)
from app.services.control_room.business_council_actors import (
    CHECKER_PERMISSIONS,
    SYSTEM_MAKER,
    CouncilMaker,
    is_council_checker,
    require_council_distinct_actors,
)


def decision_maker(decision: Mapping[str, Any]) -> CouncilMaker | None:
    person = optional_actor_id(decision.get("created_by_id"))
    if person is not None:
        return CouncilMaker.person(person)
    if str(decision.get("created_by") or "").strip() == SYSTEM_MAKER:
        return CouncilMaker.system()
    return None


async def require_distinct_item_approver(
    conn: Any,
    *,
    user: Mapping[str, Any],
    decision: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
) -> None:
    decision_id = optional_actor_id(decision.get("id"))
    maker = decision_maker(decision)
    if maker is None:
        raise ItemApprovalDenied(NO_AUTHOR, decision_id=decision_id)
    checker = optional_actor_id(user.get("id"))
    if checker is None or not is_council_checker(user):
        raise ItemApprovalDenied(APPROVER_PERMISSION, decision_id=decision_id, maker=maker)
    try:
        require_council_distinct_actors(maker, checker)
    except HTTPException:
        raise ItemApprovalDenied(
            SELF_APPROVAL, decision_id=decision_id, maker=maker
        ) from None
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    for permission in CHECKER_PERMISSIONS:
        if not tenant_id or not await actor_has_current_permission(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=checker,
            permission=permission,
        ):
            raise ItemApprovalDenied(
                APPROVER_PERMISSION, decision_id=decision_id, maker=maker
            )
    if maker.origin == "system":
        from app.services.control_room.business_council_view import (
            authored_threshold_keys,
            checker_influenced,
        )

        authored = await authored_threshold_keys(
            conn, workspace_id=workspace_id, user_id=checker
        )
        if checker_influenced(item, authored):
            raise ItemApprovalDenied(
                CHECKER_INFLUENCED, decision_id=decision_id, maker=maker
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
    "decision_maker",
    "record_item_approval_denial",
    "require_distinct_item_approver",
)
