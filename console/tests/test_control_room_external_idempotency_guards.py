from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.adapters.sap_hcm_adapter import SapHcmAdapter
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
    acquire_action_reservation,
)
from app.services.control_room.business_external_projection import (
    reserved_action_response,
)


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "decision_id": 42,
        "source_dataset": "gold_people",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
    }


@pytest.mark.asyncio
async def test_stale_remote_attempt_reservation_is_not_reclaimed():
    stale = datetime.now(UTC) - timedelta(minutes=10)
    db = AsyncMock()
    db.fetchrow.side_effect = [
        None,
        {
            "id": 7,
            "status": "pending",
            "updated_at": stale,
            "metadata": {"remote_attempt": {"status": "started"}},
        },
    ]

    result = await acquire_action_reservation(
        db,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        item=_item(),
        template_id="external-template",
        adapter_name="IdempotentAdapter",
        operation="execute",
    )

    assert result.state is ReservationState.IN_PROGRESS
    assert db.fetchrow.await_count == 2


def test_replay_of_remote_attempt_returns_reconciliation_code():
    reservation = ActionReservation(
        id=7,
        effective_key="cr-action:v1:remote-started",
        state=ReservationState.IN_PROGRESS,
        row={
            "id": 7,
            "status": "pending",
            "metadata": {"remote_attempt": {"status": "ambiguous"}},
        },
    )

    with pytest.raises(HTTPException) as exc:
        reserved_action_response(
            reservation,
            item={**_item(), "execution_status": "dry_run_validated"},
            payload={},
            action_run_public=lambda row: dict(row),
            details=lambda value: dict(value or {}),
            project_item=lambda item, *_args, **_kwargs: item,
            omega_builder=lambda item: item,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "external_action_pending_reconciliation"


def test_sap_hcm_sends_idempotency_key_as_header():
    calls: list[dict] = []

    class Response:
        status_code = 200
        text = "{}"
        headers = {"x-csrf-token": "csrf"}

        def json(self):
            return {"ok": True}

    def fake_request(*_args, **kwargs):
        calls.append(kwargs)
        return Response()

    with patch(
        "app.services.adapters.sap_hcm_adapter.egress_guard.pinned_request_sync",
        side_effect=fake_request,
    ):
        result = SapHcmAdapter().execute(
            {
                "idempotency_key": "idem-123",
                "action_payload": {"sap_hcm": {"pernr": "100"}},
            },
            {
                "base_url": "https://sap.example.test",
                "token": "secret-token",
            },
            dry_run=False,
        )

    assert result.ok is True
    assert calls[1]["headers"]["Idempotency-Key"] == "idem-123"
