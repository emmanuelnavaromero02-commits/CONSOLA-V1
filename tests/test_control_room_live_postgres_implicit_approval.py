from __future__ import annotations

import asyncio

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from app.routers import control_room as control_room_router
from app.services import control_room_service
from app.services.control_room.business_approve_with_optional_decision import (
    approve_with_optional_decision,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.db_scope import run_with_db_scope
from tests.test_control_room_live_postgres_workflows import _item, _rows, _scope
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


def _user(tenant_id: str, workspace_id: str) -> dict:
    return {
        "id": 7,
        "email": "workflow-owner@example.com",
        "role": "user",
        "workspace_role": "workspace_admin",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


async def _seed(dsn: str, item_id: str) -> tuple[dict, dict]:
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item = _item(item_id, tenant_id, workspace_id)
        await persist_item_rows(
            conn,
            _rows([item], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        return _user(tenant_id, workspace_id), {**item, "owner_user_id": 7}
    finally:
        await conn.close()


async def _approve(
    pool: asyncpg.Pool,
    *,
    user: dict,
    item: dict,
    post_link_hook=None,
):
    return await approve_with_optional_decision(
        pool,
        user=user,
        item=item,
        decision_id=None,
        lessons=("Keep measured controls",),
        confidence=0.9,
        run_scoped=run_with_db_scope,
        ensure_item_row=control_room_service._ensure_item_row,
        record_item_event=control_room_service._record_item_event,
        create_and_link=control_room_service._create_and_link_business_decision,
        approve_item=control_room_service._approve_business_item,
        link_decision=control_room_service.link_control_room_decision,
        approve_link=control_room_service.approve_control_room_decision,
        post_link_hook=post_link_hook,
    )


async def _state(dsn: str, user: dict, item_id: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        item = await conn.fetchrow(
            """SELECT status, decision_id FROM control_room_items
                WHERE workspace_id=$1 AND item_id=$2""",
            user["active_workspace_id"],
            item_id,
        )
        decision_count = await conn.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id=$1",
            user["active_workspace_id"],
        )
        actions = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE da.action_text='Decision creada desde Sala de Control'
                ) AS created,
                COUNT(*) FILTER (
                    WHERE da.action_text LIKE 'Aprobacion de recomendacion OMEGA:%'
                ) AS approved
              FROM decision_actions da
              JOIN decisions d ON d.id=da.decision_id
             WHERE d.workspace_id=$1
            """,
            user["active_workspace_id"],
        )
        events = await conn.fetch(
            """SELECT event_type, COUNT(*) AS count
                 FROM control_room_item_events
                WHERE workspace_id=$1 AND item_id=$2
                GROUP BY event_type""",
            user["active_workspace_id"],
            item_id,
        )
        lessons = await conn.fetchval(
            """SELECT COUNT(*) FROM control_room_lessons
                WHERE workspace_id=$1 AND item_id=$2""",
            user["active_workspace_id"],
            item_id,
        )
        return {
            "status": item["status"],
            "decision_id": item["decision_id"],
            "decisions": decision_count,
            "created_actions": actions["created"],
            "approved_actions": actions["approved"],
            "events": {row["event_type"]: row["count"] for row in events},
            "lessons": lessons,
        }
    finally:
        await conn.close()


def _assert_single_workflow(state: dict, *, lessons: int) -> None:
    assert state["status"] == "approved"
    assert state["decision_id"] is not None
    counts = state["decisions"], state["created_actions"], state["approved_actions"]
    assert counts == (1, 1, 1)
    assert state["events"] == dict(decision_created=1, approved=1, lesson_recorded=1)
    assert state["lessons"] == lessons


def _app(user: dict) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def current_user():
        return user

    app.dependency_overrides[control_room_router.require_authenticated] = current_user
    app.include_router(control_room_router.router)
    return app


@pytest.mark.asyncio
async def test_live_empty_body_approval_is_atomic_and_returns_200(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-empty-body")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    audit = AsyncMock()
    try:
        with (
            patch.object(
                control_room_service,
                "_item_for_mutation",
                AsyncMock(return_value=item),
            ),
            patch.object(
                control_room_service.auth, "pool", AsyncMock(return_value=pool)
            ),
            patch.object(control_room_service.audit_service, "record_event", audit),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(user)),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    f"/api/control-room/items/{item['id']}/approve",
                    headers={"authorization": "Bearer test"},
                    content=b"",
                )
    finally:
        await pool.close()

    assert response.status_code == 200, response.text
    assert response.json()["approved"] is True
    state = await _state(postgres_with_real_init_schema, user, item["id"])
    assert state["decision_id"] == response.json()["decision_id"]
    _assert_single_workflow(state, lessons=2)
    assert [call.kwargs["action"] for call in audit.await_args_list] == [
        "control_room.decision.create",
        "control_room.approve",
    ]


@pytest.mark.asyncio
async def test_live_post_link_failpoint_rolls_back_all_workflow_rows(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-rollback")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)

    async def fail(_conn, _decision_id):
        raise RuntimeError("post-link")

    try:
        with pytest.raises(RuntimeError, match="post-link"):
            await _approve(pool, user=user, item=item, post_link_hook=fail)
    finally:
        await pool.close()

    state = await _state(postgres_with_real_init_schema, user, item["id"])
    assert state == {
        "status": "open",
        "decision_id": None,
        "decisions": 0,
        "created_actions": 0,
        "approved_actions": 0,
        "events": {},
        "lessons": 0,
    }


@pytest.mark.asyncio
async def test_live_two_implicit_approvals_create_one_workflow(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-concurrent")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=2, max_size=4)
    barrier = asyncio.Barrier(2)

    async def run():
        await barrier.wait()
        try:
            return await _approve(pool, user=user, item=item)
        except HTTPException as exc:
            return exc

    try:
        results = await asyncio.gather(run(), run())
    finally:
        await pool.close()

    assert sorted(
        result.status_code if isinstance(result, HTTPException) else 200
        for result in results
    ) == [200, 409]
    state = await _state(postgres_with_real_init_schema, user, item["id"])
    _assert_single_workflow(state, lessons=1)


@pytest.mark.asyncio
async def test_live_refresh_waits_for_atomic_implicit_approval(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-refresh-race")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=2, max_size=4)
    linked = asyncio.Event()
    release = asyncio.Event()
    refresh_started = asyncio.Event()

    async def hold_after_link(_conn, _decision_id):
        linked.set()
        await release.wait()

    async def refresh():
        refresh_started.set()

        async def write(conn, _tenant_id, _workspace_id):
            await persist_item_rows(
                conn,
                _rows([item], user["active_tenant_id"], user["active_workspace_id"]),
                owner_scope_id=7,
            )

        await run_with_db_scope(pool, user, write)

    approval_task = asyncio.create_task(
        _approve(pool, user=user, item=item, post_link_hook=hold_after_link)
    )
    await linked.wait()
    refresh_task = asyncio.create_task(refresh())
    await refresh_started.wait()
    await asyncio.sleep(0.1)
    assert not refresh_task.done()
    release.set()
    try:
        await asyncio.gather(approval_task, refresh_task)
    finally:
        await pool.close()

    state = await _state(postgres_with_real_init_schema, user, item["id"])
    _assert_single_workflow(state, lessons=1)
