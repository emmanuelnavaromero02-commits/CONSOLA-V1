from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services import control_room_service
from app.services.adapters.base import (
    AdapterConfigurationError,
    AdapterExecutionError,
)
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from app.services.control_room.business_external_outcome import (
    AMBIGUOUS_OUTCOME_ERROR_CODE,
    PENDING_RECONCILIATION_CODE,
)
from control_room_external_authority import (
    external_authority,
    patch_started_revalidation,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
IDEMPOTENCY_KEY = "cr-action:v1:ambiguous-outcome"


def _item() -> dict:
    return {
        "id": "item-1",
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
        id=91,
        effective_key=IDEMPOTENCY_KEY,
        state=ReservationState.ACQUIRED,
        row={},
    )


@dataclass
class OutcomeRun:
    response: dict
    adapter: Mock
    mark_ambiguous: AsyncMock
    record_execution: AsyncMock
    complete_reservation: AsyncMock
    set_execution_status: AsyncMock


async def _run_adapter_error(
    error: Exception,
    *,
    before_started: bool = False,
    conversion_error: bool = False,
) -> OutcomeRun:
    adapter = Mock(supports_idempotency=True)
    if conversion_error:
        adapter.execute.return_value = {"ok": True}
        result_converter = Mock(side_effect=error)
    else:
        adapter.execute.side_effect = error
        result_converter = control_room_service._adapter_result_to_dict
    mark_ambiguous = AsyncMock(return_value={"id": 91, "status": "pending"})
    record_execution = AsyncMock(return_value={"id": 101})
    complete_reservation = AsyncMock(return_value={"id": 91, "status": "failed"})
    set_execution_status = AsyncMock()
    credentials = Mock(return_value={})
    if before_started:
        credentials.side_effect = error
    item = _item()
    template = {"template_id": "sap_hcm_it0008", "cartridge_id": "sap_hcm"}
    payload = {}
    authority = external_authority(item, template, payload)

    with (
        patch_started_revalidation(control_room_service, authority),
        patch.object(
            control_room_service,
            "_record_writeback_audit_event",
            new=AsyncMock(),
        ),
        patch.object(
            control_room_service,
            "_writeback_credentials_for_action",
            new=credentials,
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
            "_adapter_result_to_dict",
            new=result_converter,
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
            new=set_execution_status,
        ),
        patch.object(
            control_room_service,
            "_record_item_event",
            new=AsyncMock(),
        ),
    ):
        response = await control_room_service._execute_external_writeback(
            AsyncMock(),
            user=USER,
            item=item,
            template=template,
            payload=payload,
            reservation=_reservation(),
            ip=None,
            user_agent=None,
            authority=authority,
        )

    return OutcomeRun(
        response=response,
        adapter=adapter,
        mark_ambiguous=mark_ambiguous,
        record_execution=record_execution,
        complete_reservation=complete_reservation,
        set_execution_status=set_execution_status,
    )


def _assert_pending_reconciliation(run: OutcomeRun) -> None:
    assert run.response["_http_error_status"] == 409
    detail = run.response["_http_error_detail"]
    assert detail["code"] == PENDING_RECONCILIATION_CODE
    assert detail["reservation_id"] == 91
    assert detail["idempotency_key"] == IDEMPOTENCY_KEY
    assert detail["remote_outcome"] == "ambiguous"
    assert detail["reconciliation_required"] is True
    run.mark_ambiguous.assert_awaited_once()
    assert (
        run.mark_ambiguous.await_args.kwargs["error_code"]
        == AMBIGUOUS_OUTCOME_ERROR_CODE
    )
    run.record_execution.assert_not_awaited()
    run.complete_reservation.assert_not_awaited()
    run.set_execution_status.assert_not_awaited()
    assert run.adapter.execute.call_args.args[0]["idempotency_key"] == IDEMPOTENCY_KEY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AdapterExecutionError("request timed out"),
        AdapterExecutionError("transport timeout", status_code=503),
    ],
    ids=["timeout-without-response", "http-503"],
)
async def test_timeout_or_503_after_started_requires_reconciliation(error):
    run = await _run_adapter_error(error)

    _assert_pending_reconciliation(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 504, 429])
async def test_transient_remote_status_after_started_requires_reconciliation(
    status_code,
):
    run = await _run_adapter_error(
        AdapterExecutionError("transient remote failure", status_code=status_code)
    )

    _assert_pending_reconciliation(run)
    assert run.response["_http_error_detail"]["remote_status"] == status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
async def test_definitive_4xx_after_started_is_failed(status_code):
    run = await _run_adapter_error(
        AdapterExecutionError("request rejected", status_code=status_code)
    )

    assert run.response["_http_error_status"] == 502
    run.mark_ambiguous.assert_not_awaited()
    assert run.record_execution.await_args.kwargs["status"] == "failed"
    assert run.complete_reservation.await_args.kwargs["status"] == "failed"
    assert run.set_execution_status.await_args.kwargs["execution_status"] == "failed"


@pytest.mark.asyncio
async def test_adapter_configuration_error_is_failed_without_reconciliation():
    run = await _run_adapter_error(
        AdapterConfigurationError("connection is missing base_url")
    )

    assert run.response["_http_error_status"] == 502
    run.mark_ambiguous.assert_not_awaited()
    assert run.complete_reservation.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_error_before_remote_attempt_started_is_failed():
    run = await _run_adapter_error(
        RuntimeError("credentials unavailable"),
        before_started=True,
    )

    assert run.response["_http_error_status"] == 502
    run.mark_ambiguous.assert_not_awaited()
    run.adapter.execute.assert_not_called()
    assert run.complete_reservation.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_generic_error_during_adapter_requires_reconciliation():
    run = await _run_adapter_error(RuntimeError("connection reset after send"))

    _assert_pending_reconciliation(run)
    assert run.adapter.execute.call_count == 1


@pytest.mark.asyncio
async def test_result_conversion_error_after_success_requires_reconciliation():
    run = await _run_adapter_error(
        RuntimeError("invalid adapter result"), conversion_error=True
    )

    _assert_pending_reconciliation(run)
    assert run.adapter.execute.call_count == 1
