from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_replay import action_reservation_contract
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)


def authority_audit_for_test(
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    template_id: str,
    input_payload: Mapping[str, Any],
) -> dict[str, Any]:
    contract = action_reservation_contract(
        workspace_id=str(user["active_workspace_id"]),
        item=item,
        template_id=template_id,
        operation="execute",
        authorization_contract=execution_authorization_contract(user),
        input_payload=input_payload,
    )
    return {
        "version": "control-room-authority-audit/v1",
        "binding_id": "live-test-binding",
        "key_id": "live-test-key",
        "issued_at": "2026-07-26T12:00:00Z",
        "expires_at": "2026-07-26T12:15:00Z",
        "observation_fingerprint": contract["fingerprint"],
        "execution_target_digest": contract["execution_target_digest"],
        "template_contract_digest": contract["template_contract_digest"],
        "input_payload_digest": contract["input_payload_digest"],
    }


__all__ = ("authority_audit_for_test",)
