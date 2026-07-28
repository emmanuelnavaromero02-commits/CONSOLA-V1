from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import workspace_scope
from app.services.control_room.business_action_replay import matching_action_replay
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.business_external_receipt_contract import (
    reservation_authority_audit_valid,
)


def _is_changed(error: HTTPException) -> bool:
    return (
        error.status_code == 409
        and isinstance(error.detail, Mapping)
        and (error.detail.get("code") == "item_business_state_changed")
    )


async def authoritative_context_or_completed_replay(
    authority_helpers: Mapping[str, Any],
    conn: Any,
    *,
    user: Mapping[str, Any],
    expected_item: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
    template_id: str,
    binding_id: str,
    item_builder: Any,
    payload_builder: Any,
    clock: Any = None,
):
    authority_audit = authority_helpers["_authority_audit"]
    build_context = authority_helpers["_build_context"]
    changed = authority_helpers["_changed"]
    clock_snapshot = authority_helpers["_clock_snapshot"]
    required_binding = authority_helpers["_required_binding"]
    lock_item = authority_helpers["lock_authoritative_business_item"]
    lock_context = authority_helpers["lock_authoritative_execution_context"]

    try:
        context = await lock_context(
            conn,
            user=user,
            expected_item=expected_item,
            expected_payload=expected_payload,
            template_id=template_id,
            binding_id=binding_id,
            item_builder=item_builder,
            payload_builder=payload_builder,
            clock=clock,
        )
        return context, None
    except HTTPException as error:
        if not _is_changed(error):
            raise

    locked = await lock_item(
        conn,
        user=user,
        item=expected_item,
        allow_completed_execution_replay=True,
        decision_id=int(expected_item["decision_id"]),
    )
    fixed_clock = clock_snapshot(clock)
    authoritative = item_builder(locked) if locked is not None else None
    if (
        authoritative is None
        or str(expected_item.get("execution_status") or "").lower()
        != "dry_run_validated"
        or str(authoritative.get("execution_status") or "").lower() != "executed"
    ):
        raise changed()
    expected_binding = required_binding(
        expected_item,
        user,
        template_id=template_id,
        binding_id=binding_id,
        clock=fixed_clock,
    )
    context = build_context(
        authoritative,
        user,
        template_id=template_id,
        binding_id=binding_id,
        payload_builder=payload_builder,
        clock=fixed_clock,
    )
    if context.authority_audit != authority_audit(expected_binding, expected_payload):
        raise changed()
    _tenant_id, workspace_id = workspace_scope(user)
    replay = await matching_action_replay(
        conn,
        workspace_id=workspace_id,
        item=context.item,
        template_id=template_id,
        operation="execute",
        authorization_contract=execution_authorization_contract(user),
        input_payload=context.payload,
    )
    if replay is None:
        raise changed()
    key, row = replay
    if str(row.get("status") or "") != "completed" or not (
        reservation_authority_audit_valid(row, context.authority_audit)
    ):
        raise changed()
    return context, ActionReservation(
        id=int(row["id"]),
        effective_key=key,
        state=ReservationState.COMPLETED,
        row=dict(row),
    )


__all__ = ("authoritative_context_or_completed_replay",)
