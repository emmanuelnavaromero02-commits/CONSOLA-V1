from __future__ import annotations

import asyncio

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_approval import approve_business_item
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
    persist_option_selection,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_repository import (
    approve_control_room_decision,
    link_control_room_decision,
)
from app.services.control_room.business_workflow_provenance import WorkflowStage
from app.services.db_scope import SET_SCOPE_SQL
from tests.test_control_room_live_postgres_workflows import (
    AsyncNoop,
    _item,
    _rows,
    _scope,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _linked_item(dsn: str, item_id: str):
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item = _item(item_id, tenant_id, workspace_id)
        await persist_item_rows(
            conn,
            _rows([item], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        decision = await create_and_link_decision(
            conn,
            user={"id": 7, "email": "workflow-owner@example.com"},
            item={**item, "owner_user_id": 7},
            workspace_id=workspace_id,
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
        )
        linked = {
            **item,
            "owner_user_id": 7,
            "decision_id": int(decision["id"]),
            "status": "decision_created",
        }
        return tenant_id, workspace_id, linked
    finally:
        await conn.close()


def _user(tenant_id: str, workspace_id: str) -> dict:
    return {
        "id": 7,
        "email": "workflow-owner@example.com",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


async def _approve(conn, *, user, item, workspace_id):
    return await approve_business_item(
        conn,
        user=user,
        item=item,
        workspace_id=workspace_id,
        decision_id=int(item["decision_id"]),
        lessons=("Keep measured controls",),
        confidence=0.9,
        ensure_item_row=AsyncNoop(),
        link_decision=link_control_room_decision,
        approve_link=approve_control_room_decision,
    )


async def _tx(dsn: str, tenant_id: str, workspace_id: str, work):
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            return await work(conn)
    finally:
        await conn.close()


async def _assert_single_approval(dsn: str, workspace_id: str, item: dict):
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, metadata FROM control_room_items WHERE workspace_id=$1 AND item_id=$2",
            workspace_id,
            item["id"],
        )
        assert row["status"] == "approved"
        assert row["metadata"]["decision_eligibility_provenance"]["stage"] == "approved"
        assert (
            await conn.fetchval(
                """SELECT COUNT(*) FROM decision_actions
                 WHERE decision_id=$1 AND action_text LIKE 'Aprobacion de recomendacion OMEGA:%'""",
                item["decision_id"],
            )
            == 1
        )
        assert (
            await conn.fetchval(
                """SELECT COUNT(*) FROM control_room_item_events
                 WHERE workspace_id=$1 AND item_id=$2 AND event_type='approved'""",
                workspace_id,
                item["id"],
            )
            == 1
        )
        assert (
            await conn.fetchval(
                """SELECT COUNT(*) FROM control_room_lessons
                 WHERE workspace_id=$1 AND item_id=$2""",
                workspace_id,
                item["id"],
            )
            == 1
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_concurrent_approval_has_one_winner(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, "p16-double-approval"
    )
    user = _user(tenant_id, workspace_id)
    barrier = asyncio.Barrier(2)

    async def approve():
        await barrier.wait()
        try:
            return await _tx(
                omega_console_live_dsn,
                tenant_id,
                workspace_id,
                lambda conn: _approve(
                    conn, user=user, item=item, workspace_id=workspace_id
                ),
            )
        except HTTPException as exc:
            return exc

    results = await asyncio.gather(approve(), approve())
    assert sorted(
        result.status_code if isinstance(result, HTTPException) else 200
        for result in results
    ) == [200, 409]
    await _assert_single_approval(postgres_with_real_init_schema, workspace_id, item)


@pytest.mark.asyncio
async def test_live_option_waiting_on_approval_cannot_downgrade_stage(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, "p16-option-approval"
    )
    user = _user(tenant_id, workspace_id)
    approval_locked = asyncio.Event()
    allow_approval = asyncio.Event()

    async def approve_first():
        async def work(conn):
            await lock_authoritative_business_item(
                conn,
                user=user,
                item=item,
                decision_id=int(item["decision_id"]),
                allowed_stages=(WorkflowStage.DECISION_CREATED,),
            )
            approval_locked.set()
            await allow_approval.wait()
            return await _approve(conn, user=user, item=item, workspace_id=workspace_id)

        return await _tx(omega_console_live_dsn, tenant_id, workspace_id, work)

    async def select_option():
        await approval_locked.wait()
        allow_approval.set()

        async def work(conn):
            return await persist_option_selection(
                conn,
                user=user,
                item=item,
                workspace_id=workspace_id,
                option_id="review",
                terminal_statuses=("approved", "executed", "resolved", "dismissed"),
                ensure_item_row=AsyncNoop(),
                record_item_event=AsyncNoop(),
            )

        try:
            return await _tx(omega_console_live_dsn, tenant_id, workspace_id, work)
        except HTTPException as exc:
            return exc

    approved, option = await asyncio.gather(approve_first(), select_option())
    assert approved["id"] is not None
    assert isinstance(option, HTTPException) and option.status_code == 409
    await _assert_single_approval(postgres_with_real_init_schema, workspace_id, item)
