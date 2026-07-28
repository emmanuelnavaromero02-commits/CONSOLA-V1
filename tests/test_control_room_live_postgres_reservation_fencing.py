from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from app.services.control_room.business_action_attempt import (
    mark_remote_attempt_started,
    remote_attempt_status,
)
from app.services.control_room.business_action_replay import (
    action_reservation_contract,
)
from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_action_reservation,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.business_external_receipt_contract import (
    authority_audit_matches_contract,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_reservation_lease import (
    reservation_lease_token,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_live_authority import authority_audit_for_test
from tests.test_control_room_live_postgres_p15 import _user
from tests.test_control_room_live_postgres_workflows import _item, _rows, _scope
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


TEMPLATE_ID = "prepare_hcm_access_review"
PAYLOAD = {"mode": "execute_live", "action_payload": {}}


async def _mark(
    dsn: str,
    *,
    tenant_id: str,
    workspace_id: str,
    reservation_id: int,
    effective_key: str,
    lease_token: str,
) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            return await mark_remote_attempt_started(
                conn,
                workspace_id=workspace_id,
                reservation_id=reservation_id,
                effective_key=effective_key,
                adapter="IdempotentAdapter",
                target="sap_hcm",
                lease_token=lease_token,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_stale_reclaim_rotates_token_and_fences_prior_owner(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    seed = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(seed)
        item = _item(f"reservation-fencing-{uuid4().hex}", tenant_id, workspace_id)
        await persist_item_rows(
            seed,
            _rows([item], tenant_id, workspace_id),
            owner_scope_id=7,
        )
    finally:
        await seed.close()

    user = _user(tenant_id, workspace_id)
    authorization = execution_authorization_contract(user)
    old_audit = authority_audit_for_test(
        user=user,
        item=item,
        template_id=TEMPLATE_ID,
        input_payload=PAYLOAD,
    )
    new_audit = {
        **old_audit,
        "binding_id": "live-test-binding-refreshed",
        "key_id": "live-test-key-refreshed",
        "issued_at": "2026-07-26T12:15:00Z",
        "expires_at": "2026-07-26T12:30:00Z",
    }
    contract = action_reservation_contract(
        workspace_id=workspace_id,
        item=item,
        template_id=TEMPLATE_ID,
        operation="execute",
        authorization_contract=authorization,
        input_payload=PAYLOAD,
    )
    assert authority_audit_matches_contract({"authority_audit": old_audit}, contract)
    assert authority_audit_matches_contract({"authority_audit": new_audit}, contract)

    first_conn = await asyncpg.connect(omega_console_live_dsn)
    try:
        async with first_conn.transaction():
            await first_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            first = await acquire_action_reservation(
                first_conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                item=item,
                template_id=TEMPLATE_ID,
                adapter_name="IdempotentAdapter",
                operation="execute",
                input_payload=PAYLOAD,
                actor_id=7,
                authorization_contract=authorization,
                authority_audit=old_audit,
            )
    finally:
        await first_conn.close()

    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await admin.execute(
            "UPDATE action_runs SET updated_at=NOW()-INTERVAL '10 minutes' "
            "WHERE workspace_id=$1::uuid AND id=$2",
            workspace_id,
            first.id,
        )
    finally:
        await admin.close()

    retry_conn = await asyncpg.connect(omega_console_live_dsn)
    try:
        async with retry_conn.transaction():
            await retry_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            retry = await acquire_action_reservation(
                retry_conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                item=item,
                template_id=TEMPLATE_ID,
                adapter_name="IdempotentAdapter",
                operation="execute",
                input_payload=PAYLOAD,
                actor_id=7,
                authorization_contract=authorization,
                authority_audit=new_audit,
                authority_refresh_guard=lambda: None,
            )
    finally:
        await retry_conn.close()

    first_token = reservation_lease_token(first.row)
    retry_token = reservation_lease_token(retry.row)
    assert first.state == ReservationState.ACQUIRED
    assert retry.state == ReservationState.ACQUIRED
    assert retry.id == first.id
    assert retry.effective_key == first.effective_key
    assert first_token and retry_token and retry_token != first_token

    with pytest.raises(
        RuntimeError, match="remote attempt reservation was not pending"
    ):
        await _mark(
            omega_console_live_dsn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=first.id,
            effective_key=first.effective_key,
            lease_token=first_token,
        )

    marked = await _mark(
        omega_console_live_dsn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        reservation_id=retry.id,
        effective_key=retry.effective_key,
        lease_token=retry_token,
    )
    assert remote_attempt_status(marked) == "started"

    with pytest.raises(
        RuntimeError, match="remote attempt reservation was not pending"
    ):
        await _mark(
            omega_console_live_dsn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            reservation_id=retry.id,
            effective_key=retry.effective_key,
            lease_token=retry_token,
        )

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        row = await check.fetchrow(
            "SELECT status, metadata->>'reservation_lease_token' AS lease_token, "
            "metadata->'authority_audit'->>'binding_id' AS binding_id, "
            "metadata->'remote_attempt'->>'status' AS attempt_status, "
            "(SELECT COUNT(*) FROM action_runs WHERE workspace_id=$1::uuid "
            "AND idempotency_key=$2) AS run_count "
            "FROM action_runs WHERE workspace_id=$1::uuid AND id=$3",
            workspace_id,
            retry.effective_key,
            retry.id,
        )
    finally:
        await check.close()

    assert dict(row) == {
        "status": "pending",
        "lease_token": retry_token,
        "binding_id": new_audit["binding_id"],
        "attempt_status": "started",
        "run_count": 1,
    }
