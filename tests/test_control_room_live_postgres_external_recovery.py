from __future__ import annotations

from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.services import control_room_service as service
from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_action_reservation,
    acquire_guarded_action_reservation,
)
from app.services.control_room.business_action_failure import (
    finalize_aborted_action_reservation,
)
from app.services.control_room.business_action_attempt import (
    mark_remote_attempt_started,
    remote_attempt_status,
)
from app.services.control_room.business_external_effect import (
    RemoteSideEffectCommitted,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.test_control_room_live_postgres_p15 import (
    _linked_item,
    _mutation_item,
    _seed_matching_dry_run,
    _template,
    _user,
)
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_stale_external_reservation_reclaims_same_row_and_key(
    postgres_with_real_init_schema: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "p15-external-recovery",
    )
    first_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await first_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        first = await acquire_action_reservation(
            first_conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item=item,
            template_id="prepare_hcm_access_review",
            adapter_name="IdempotentAdapter",
            operation="execute",
        )
        await first_conn.execute(
            """UPDATE action_runs
                  SET updated_at = NOW() - INTERVAL '10 minutes'
                WHERE workspace_id=$1 AND id=$2""",
            workspace_id,
            first.id,
        )
    finally:
        await first_conn.close()

    retry_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await retry_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        retry = await acquire_action_reservation(
            retry_conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item=item,
            template_id="prepare_hcm_access_review",
            adapter_name="IdempotentAdapter",
            operation="execute",
        )
        count = await retry_conn.fetchval(
            """SELECT COUNT(*) FROM action_runs
                WHERE workspace_id=$1 AND idempotency_key=$2""",
            workspace_id,
            first.effective_key,
        )
    finally:
        await retry_conn.close()

    assert retry.state is ReservationState.ACQUIRED
    assert retry.id == first.id
    assert retry.effective_key == first.effective_key
    assert count == 1


@pytest.mark.asyncio
async def test_live_durable_remote_attempt_is_never_reclaimed(
    postgres_with_real_init_schema: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "p15-external-durable-attempt",
    )
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await setup.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        first = await acquire_action_reservation(
            setup,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item=item,
            template_id="prepare_hcm_access_review",
            adapter_name="IdempotentAdapter",
            operation="execute",
        )
        await mark_remote_attempt_started(
            setup,
            workspace_id=workspace_id,
            reservation_id=first.id,
            effective_key=first.effective_key,
            adapter="IdempotentAdapter",
            target="sap_hcm",
        )
        await setup.execute(
            """UPDATE action_runs
                  SET updated_at = NOW() - INTERVAL '10 minutes'
                WHERE workspace_id=$1 AND id=$2""",
            workspace_id,
            first.id,
        )
    finally:
        await setup.close()

    retry_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await retry_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        retry = await acquire_action_reservation(
            retry_conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item=item,
            template_id="prepare_hcm_access_review",
            adapter_name="IdempotentAdapter",
            operation="execute",
        )
    finally:
        await retry_conn.close()

    assert retry.state is ReservationState.IN_PROGRESS
    assert retry.id == first.id
    assert remote_attempt_status(retry.row) == "started"


@pytest.mark.asyncio
async def test_live_remote_receipt_projects_authoritative_item_once(
    postgres_with_real_init_schema: str, monkeypatch: pytest.MonkeyPatch
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "external-finalizer-projection",
    )
    user = _user(tenant_id, workspace_id)
    template = _template("prepare_hcm_access_review")
    payload = {"mode": "execute_live", "action_payload": {}}
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template["template_id"],
    )
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await setup.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        await setup.execute(
            """UPDATE control_room_items
                  SET execution_status='dry_run_validated'
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item["id"],
        )
        reservation = await acquire_guarded_action_reservation(
            setup,
            user=user,
            item=_mutation_item(item),
            template_id=template["template_id"],
            adapter_name="Adapter",
            operation="execute",
            input_payload=payload,
            persist_item=service._ensure_item_row,
        )
        await mark_remote_attempt_started(
            setup,
            workspace_id=workspace_id,
            reservation_id=reservation.id,
            effective_key=reservation.effective_key,
            adapter="Adapter",
            target="replicon",
        )
    finally:
        await setup.close()

    class Adapter(service.BaseAdapter):
        supports_idempotency = True
        calls = 0

        async def execute(self, action_data, credentials, dry_run=True):
            type(self).calls += 1
            return service.ExecutionResult(True, "executed", "ok", {"id": "remote-1"})

    monkeypatch.setattr(
        service.WriteBackAdapterFactory,
        "get_adapter",
        classmethod(lambda _cls, _template_type: Adapter()),
    )
    execute_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        with monkeypatch.context() as local_failure:
            local_failure.setattr(
                service,
                "_record_action_execution",
                AsyncMock(side_effect=ConnectionError("late local failure")),
            )
            captured_error = None
            try:
                async with execute_conn.transaction():
                    await execute_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                    await service._execute_external_writeback(
                        execute_conn,
                        user=user,
                        item=_mutation_item(item),
                        template=template,
                        payload=payload,
                        reservation=reservation,
                        ip=None,
                        user_agent=None,
                    )
            except BaseException as exc:
                captured_error = exc
            assert type(captured_error).__name__ == RemoteSideEffectCommitted.__name__
    finally:
        await execute_conn.close()

    finalize_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        async with finalize_conn.transaction():
            await finalize_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            finalized = await finalize_aborted_action_reservation(
                finalize_conn,
                workspace_id=workspace_id,
                reservation=reservation,
                error=captured_error,
            )
    finally:
        await finalize_conn.close()

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        execution_state = await check.fetchrow(
            """SELECT execution_status,
                      metadata->'decision_eligibility_provenance'->>'stage'
                          AS workflow_stage
                 FROM control_room_items
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item["id"],
        )
        run_count = await check.fetchval(
            """SELECT COUNT(*) FROM action_runs
                WHERE workspace_id=$1 AND item_id=$2 AND mode='execute'""",
            workspace_id,
            item["id"],
        )
        projection_status = await check.fetchval(
            """SELECT execution_result->>'local_projection_status'
                  FROM action_runs
                 WHERE workspace_id=$1 AND id=$2""",
            workspace_id,
            reservation.id,
        )
    finally:
        await check.close()

    assert Adapter.calls == 1
    assert dict(execution_state) == {
        "execution_status": "executed",
        "workflow_stage": "executed",
    }
    assert finalized["id"] == reservation.id
    assert projection_status == "completed"
    assert run_count == 1
