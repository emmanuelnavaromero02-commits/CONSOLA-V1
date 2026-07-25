from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from app.services.control_room.business_external_projection import (
    reserved_action_response,
)


def _response(execution_result: dict[str, object]) -> dict[str, object]:
    reservation = ActionReservation(
        id=7,
        effective_key="cr-action:v1:completed",
        state=ReservationState.COMPLETED,
        row={
            "id": 7,
            "status": "completed",
            "input": {},
            "execution_result": execution_result,
        },
    )
    return reserved_action_response(
        reservation,
        item={"id": "item-1", "execution_status": "dry_run_validated"},
        payload={},
        action_run_public=lambda row: dict(row),
        details=lambda value: dict(value or {}),
        project_item=lambda item, _omega, **updates: {**item, **updates},
        omega_builder=lambda item: item,
    )


def test_completed_internal_replay_projects_committed_execution() -> None:
    response = _response({"executed": True})

    assert response["idempotent"] is True
    assert response["item"]["execution_status"] == "executed"


def test_completed_external_projection_ignores_stale_request_item() -> None:
    response = _response(
        {
            "executed": True,
            "external_write": True,
            "local_projection_status": "completed",
        }
    )

    assert response["idempotent"] is True
    assert response["item"]["execution_status"] == "executed"


def test_completed_external_receipt_without_recovery_marker_is_committed() -> None:
    response = _response({"executed": True, "external_write": True})

    assert response["idempotent"] is True
    assert response["item"]["execution_status"] == "executed"


def test_pending_external_projection_remains_blocked() -> None:
    with pytest.raises(HTTPException) as exc:
        _response(
            {
                "executed": True,
                "external_write": True,
                "local_projection_status": "pending_reconciliation",
            }
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "external_action_pending_reconciliation"
