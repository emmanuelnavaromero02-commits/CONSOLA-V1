from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service, egress_guard
from app.services.adapters.circuit_breaker import CartridgeCircuitBreaker
from app.services.adapters.sap_hcm_adapter import SapHcmAdapter
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
IDEMPOTENCY_KEY = "cr-action:v1:sap-hcm-real"


class SapResponse:
    def __init__(self, status_code: int, *, csrf_token: str | None = None):
        self.status_code = status_code
        self.headers = {"x-csrf-token": csrf_token} if csrf_token else {}
        self.text = f"HTTP {status_code}"

    def json(self):
        return {"status_code": self.status_code}


@dataclass
class SapOutcomeRun:
    response: dict
    request_methods: list[str]
    mark_ambiguous: AsyncMock
    complete_reservation: AsyncMock
    record_execution: AsyncMock


def _item() -> dict:
    return {
        "id": "item-sap-hcm",
        "kind": "anomaly",
        "decision_id": 42,
        "status": "approved",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "execution_status": "dry_run_validated",
        "source_dataset": "gold_people",
        "observation_date": "2026-07-20",
    }


def _reservation() -> ActionReservation:
    return ActionReservation(
        id=92,
        effective_key=IDEMPOTENCY_KEY,
        state=ReservationState.ACQUIRED,
        row={},
    )


async def _run_real_sap_outcome(
    *,
    csrf_outcome: int | Exception = 200,
    post_outcome: int | Exception = 201,
) -> SapOutcomeRun:
    request_methods: list[str] = []

    def fake_request(method, _url, **_kwargs):
        request_methods.append(method)
        outcome = csrf_outcome if method == "GET" else post_outcome
        if isinstance(outcome, Exception):
            raise outcome
        return SapResponse(
            outcome,
            csrf_token="csrf-token" if method == "GET" and outcome == 200 else None,
        )

    adapter = SapHcmAdapter()
    mark_started = AsyncMock(return_value={"id": 92, "status": "pending"})
    mark_ambiguous = AsyncMock(return_value={"id": 92, "status": "pending"})
    record_execution = AsyncMock(return_value={"id": 102})
    complete_reservation = AsyncMock(return_value={"id": 92, "status": "failed"})
    CartridgeCircuitBreaker.reset(SapHcmAdapter.CARTRIDGE_ID)

    try:
        with (
            patch.object(
                control_room_service,
                "require_approved_execution",
                new=AsyncMock(),
            ),
            patch.object(
                control_room_service,
                "lock_pending_action_reservation",
                new=AsyncMock(),
            ),
            patch.object(
                control_room_service,
                "_record_writeback_audit_event",
                new=AsyncMock(),
            ),
            patch.object(
                control_room_service,
                "_writeback_credentials_for_action",
                return_value={
                    "base_url": "https://sap.example",
                    "token": "sap-token",
                },
            ),
            patch.object(
                control_room_service.WriteBackAdapterFactory,
                "get_adapter",
                return_value=adapter,
            ),
            patch.object(
                control_room_service,
                "adapter_guarantees_idempotency",
                return_value=True,
            ),
            patch.object(
                control_room_service,
                "mark_remote_attempt_started",
                new=mark_started,
            ),
            patch(
                "app.services.control_room.business_external_outcome."
                "mark_remote_attempt_ambiguous",
                new=mark_ambiguous,
            ),
            patch.object(
                control_room_service,
                "_record_action_execution",
                new=record_execution,
            ),
            patch.object(
                control_room_service,
                "_complete_execute_reservation",
                new=complete_reservation,
            ),
            patch.object(
                control_room_service,
                "_set_execution_status",
                new=AsyncMock(),
            ),
            patch.object(
                control_room_service,
                "_record_item_event",
                new=AsyncMock(),
            ),
            patch(
                "app.services.adapters.sap_hcm_adapter."
                "egress_guard.pinned_request_sync",
                side_effect=fake_request,
            ),
        ):
            response = await control_room_service._execute_external_writeback(
                AsyncMock(),
                user=USER,
                item=_item(),
                template={
                    "template_id": "sap_hcm_it0008",
                    "cartridge_id": "sap_hcm",
                },
                payload={},
                reservation=_reservation(),
                ip=None,
                user_agent=None,
            )
    finally:
        CartridgeCircuitBreaker.reset(SapHcmAdapter.CARTRIDGE_ID)

    assert adapter.__class__ is SapHcmAdapter
    mark_started.assert_awaited_once()
    return SapOutcomeRun(
        response=response,
        request_methods=request_methods,
        mark_ambiguous=mark_ambiguous,
        complete_reservation=complete_reservation,
        record_execution=record_execution,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("csrf_outcome", "post_outcome", "ambiguous_calls", "request_methods"),
    [
        (200, TimeoutError("POST timed out"), 1, ["GET", "POST"]),
        (200, OSError("POST disconnected"), 1, ["GET", "POST"]),
        (
            200,
            egress_guard.EgressGuardError("response exceeds size limit"),
            1,
            ["GET", "POST"],
        ),
        (200, 0, 1, ["GET", "POST"]),
        (200, 429, 1, ["GET", "POST"]),
        (200, 503, 1, ["GET", "POST"]),
        (TimeoutError("CSRF timed out"), 201, 0, ["GET"]),
        (503, 201, 0, ["GET"]),
        (200, 400, 0, ["GET", "POST"]),
        (200, 302, 0, ["GET", "POST"]),
    ],
    ids=[
        "post-timeout",
        "post-os-error",
        "post-response-guard",
        "post-malformed-status",
        "post-429",
        "post-503",
        "csrf-timeout",
        "csrf-503",
        "post-400",
        "post-302",
    ],
)
async def test_real_sap_outcome_marks_only_ambiguous_post_failures(
    csrf_outcome,
    post_outcome,
    ambiguous_calls,
    request_methods,
):
    run = await _run_real_sap_outcome(
        csrf_outcome=csrf_outcome,
        post_outcome=post_outcome,
    )

    assert run.mark_ambiguous.await_count == ambiguous_calls
    assert run.request_methods == request_methods
    if ambiguous_calls:
        assert run.response["_http_error_status"] == 409
        assert run.response["_http_error_detail"]["idempotency_key"] == IDEMPOTENCY_KEY
        run.complete_reservation.assert_not_awaited()
        run.record_execution.assert_not_awaited()
    else:
        assert run.response["_http_error_status"] == 502
        assert run.complete_reservation.await_args.kwargs["status"] == "failed"
        assert run.record_execution.await_args.kwargs["status"] == "failed"
