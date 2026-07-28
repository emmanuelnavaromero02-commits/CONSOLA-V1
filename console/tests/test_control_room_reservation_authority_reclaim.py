from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.control_room.business_action_replay import action_reservation_contract
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationConflict,
    ReservationState,
    acquire_action_reservation,
    acquire_guarded_action_reservation,
)


def _item() -> dict:
    return {
        "id": "item-1",
        "workspace_id": "workspace-a",
        "decision_id": 42,
        "entity_kind": "employee",
        "entity_id": "employee-1",
        "source_dataset": "gold_people",
        "metadata": {
            "connection": {
                "base_url": "https://hcm.example.invalid",
                "writeback_path": "/access-review",
            }
        },
        "observed_value": 1,
        "metric_type": "count",
        "observation_date": "2026-07-20",
    }


def _contract() -> dict:
    return action_reservation_contract(
        workspace_id="workspace-a",
        item=_item(),
        template_id="prepare_hcm_access_review",
        operation="execute",
        authorization_contract=None,
    )


def _authority(binding_id: str) -> dict:
    contract = _contract()
    return {
        "version": "control-room-authority-audit/v1",
        "binding_id": binding_id,
        "key_id": "test-key",
        "issued_at": "2026-07-26T12:00:00Z",
        "expires_at": "2026-07-26T12:15:00Z",
        "observation_fingerprint": contract["fingerprint"],
        "execution_target_digest": contract["execution_target_digest"],
        "template_contract_digest": contract["template_contract_digest"],
        "input_payload_digest": contract["input_payload_digest"],
    }


def _row(*, authority: dict, remote_attempt: object = ...) -> dict:
    metadata = {
        "reservation_contract": _contract(),
        "authority_audit": authority,
        "reservation_lease_token": f"lease-{authority['binding_id']}",
    }
    if remote_attempt is not ...:
        metadata["remote_attempt"] = remote_attempt
    return {
        "id": 7,
        "status": "pending",
        "updated_at": datetime.now(UTC) - timedelta(minutes=10),
        "metadata": metadata,
    }


async def _acquire(db: AsyncMock, authority: dict):
    return await acquire_action_reservation(
        db,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        item=_item(),
        template_id="prepare_hcm_access_review",
        adapter_name="IdempotentAdapter",
        operation="execute",
        authority_audit=authority,
        authority_refresh_guard=lambda: None,
    )


@pytest.mark.asyncio
async def test_stale_unattempted_reservation_refreshes_authority_under_cas() -> None:
    old_authority = _authority("expired-binding")
    new_authority = _authority("current-binding")
    refreshed = _row(authority=new_authority)
    refreshed["updated_at"] = datetime.now(UTC)
    db = AsyncMock()
    db.fetchrow.side_effect = [None, _row(authority=old_authority), refreshed]

    result = await _acquire(db, new_authority)

    assert result.state == ReservationState.ACQUIRED
    assert result.row["metadata"]["authority_audit"] == new_authority
    update = db.fetchrow.await_args_list[2]
    sql = " ".join(update.args[0].split())
    assert "status = 'pending'" in sql
    assert "? 'remote_attempt'" in sql
    assert "reservation_lease_token" in sql
    assert '"current-binding"' in update.args[7]


@pytest.mark.asyncio
async def test_stale_remote_attempt_never_refreshes_changed_authority() -> None:
    existing = _row(
        authority=_authority("original-binding"),
        remote_attempt={"status": "started"},
    )
    db = AsyncMock()
    db.fetchrow.side_effect = [None, existing]

    with pytest.raises(ReservationConflict, match="authority audit mismatch"):
        await _acquire(db, _authority("different-binding"))

    assert db.fetchrow.await_count == 2


@pytest.mark.asyncio
async def test_live_unexpired_reservation_rejects_changed_authority() -> None:
    existing = _row(authority=_authority("original-binding"))
    existing["updated_at"] = datetime.now(UTC)
    db = AsyncMock()
    db.fetchrow.side_effect = [None, existing]

    with pytest.raises(ReservationConflict, match="authority audit mismatch"):
        await _acquire(db, _authority("different-binding"))

    assert db.fetchrow.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ({}, None, {"status": "unknown"}))
async def test_any_remote_attempt_marker_blocks_reclaim(marker: object) -> None:
    authority = _authority("same-binding")
    existing = _row(authority=authority, remote_attempt=marker)
    db = AsyncMock()
    db.fetchrow.side_effect = [None, existing]

    result = await _acquire(db, authority)

    assert result.state == ReservationState.IN_PROGRESS
    assert db.fetchrow.await_count == 2


@pytest.mark.asyncio
async def test_malformed_authority_is_rejected_before_insert() -> None:
    db = AsyncMock()

    with pytest.raises(ReservationConflict, match="authority audit is invalid"):
        await _acquire(db, {"binding_id": "incomplete"})

    db.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_rechecks_authority_after_blocking_reservation_dml() -> None:
    guard = Mock(side_effect=[None, RuntimeError("authority expired while blocked")])
    acquired = ActionReservation(
        id=9,
        effective_key="server-key",
        state=ReservationState.ACQUIRED,
        row={"id": 9, "status": "pending"},
    )
    with (
        patch(
            "app.services.control_room.business_action_reservation."
            "require_approved_execution",
            AsyncMock(),
        ),
        patch(
            "app.services.control_room.business_action_reservation."
            "require_no_legacy_action_reservation",
            AsyncMock(),
        ),
        patch(
            "app.services.control_room.business_action_reservation."
            "acquire_action_reservation",
            AsyncMock(return_value=acquired),
        ),
        pytest.raises(RuntimeError, match="authority expired while blocked"),
    ):
        await acquire_guarded_action_reservation(
            object(),
            user={
                "id": 7,
                "email": "owner@example.com",
                "active_tenant_id": "tenant-a",
                "active_workspace_id": "workspace-a",
                "_effective_permissions": [
                    "control_room.write",
                    "control_room.execute",
                ],
            },
            item=_item(),
            template_id="prepare_hcm_access_review",
            adapter_name="IdempotentAdapter",
            operation="execute",
            reservation_guard=guard,
        )

    assert guard.call_count == 2
