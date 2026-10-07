from __future__ import annotations

import asyncio
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from app.routers import control_room as control_room_router
from app.services import audit_service, auth, control_room_service
from app.services.control_room.business_approve_with_optional_decision import (
    approve_with_optional_decision,
)
from app.services.control_room.business_council_actors import SYSTEM_MAKER
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_approval_guard import (
    APPROVER_PERMISSION,
    DENIED_AUDIT_ACTION,
    IMPLICIT_SELF_APPROVAL,
    SELF_APPROVAL,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.db_scope import run_with_db_scope
from tests.control_room_action_authority_live import _new_user
from tests.test_control_room_live_postgres_workflows import AsyncNoop, _item, _rows
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _workspace(conn: asyncpg.Connection) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    tenant_id = str(
        await conn.fetchval(
            "INSERT INTO tenants(name, slug) VALUES ($1, $2) RETURNING id",
            f"Four eyes {suffix}",
            f"four-eyes-{suffix}",
        )
    )
    workspace_id = str(
        await conn.fetchval(
            "INSERT INTO workspaces(tenant_id, name) VALUES ($1, 'Four eyes') RETURNING id",
            tenant_id,
        )
    )
    return tenant_id, workspace_id, suffix


async def _admin(conn, tenant_id: str, workspace_id: str, suffix: str) -> dict:
    return await _new_user(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        workspace_role="workspace_admin",
        suffix=suffix,
    )


async def _seed(dsn: str, item_id: str) -> tuple[dict, dict]:
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id, workspace_id, suffix = await _workspace(conn)
        user = await _admin(conn, tenant_id, workspace_id, f"owner-{suffix}")
        item = {**_item(item_id, tenant_id, workspace_id), "owner_user_id": user["id"]}
        await persist_item_rows(
            conn,
            _rows([item], tenant_id, workspace_id, owner=user["id"]),
            owner_scope_id=user["id"],
        )
        return user, item
    finally:
        await conn.close()


async def _seed_linked(
    dsn: str, item_id: str, *, system_made: bool = False
) -> tuple[dict, dict, dict]:
    maker, item = await _seed(dsn, item_id)
    conn = await asyncpg.connect(dsn)
    try:
        checker = await _admin(
            conn,
            maker["active_tenant_id"],
            maker["active_workspace_id"],
            f"checker-{uuid.uuid4().hex[:8]}",
        )
        decision = await create_and_link_decision(
            conn,
            user=maker,
            item=item,
            workspace_id=maker["active_workspace_id"],
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
            system_maker=SYSTEM_MAKER if system_made else None,
        )
        linked = {**item, "decision_id": int(decision["id"]), "status": "decision_created"}
        return maker, checker, linked
    finally:
        await conn.close()


async def _approve(
    pool: asyncpg.Pool,
    *,
    user: dict,
    item: dict,
    decision_id: int | None = None,
    audit_recorder=None,
):
    with patch.object(auth, "pool", AsyncMock(return_value=pool)):
        return await approve_with_optional_decision(
            pool,
            user=user,
            item=item,
            decision_id=decision_id,
            lessons=("Keep measured controls",),
            confidence=0.9,
            run_scoped=run_with_db_scope,
            ensure_item_row=control_room_service._ensure_item_row,
            approve_item=control_room_service._approve_business_item,
            link_decision=control_room_service.link_control_room_decision,
            approve_link=control_room_service.approve_control_room_decision,
            record_audit_event=audit_recorder or audit_service.record_event,
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
        approved = await conn.fetchval(
            """
            SELECT COUNT(*)
              FROM decision_actions da
              JOIN decisions d ON d.id=da.decision_id
             WHERE d.workspace_id=$1
               AND da.action_text LIKE 'Aprobacion de recomendacion OMEGA:%'
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
            "approved_actions": approved,
            "events": {row["event_type"]: row["count"] for row in events},
            "lessons": lessons,
        }
    finally:
        await conn.close()


async def _audits(dsn: str, item_id: str) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """SELECT user_id, action, status, metadata->>'reason' AS reason
                 FROM audit_events
                WHERE resource_type='control_room_item' AND resource_id=$1
                ORDER BY id""",
            item_id,
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


def _assert_not_approved(state: dict) -> None:
    assert state["status"] == "decision_created"
    assert state["decisions"] == 1
    assert state["approved_actions"] == 0
    assert "approved" not in state["events"]
    assert state["lessons"] == 0


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
async def test_live_empty_body_approval_is_rejected_without_writes(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-empty-body")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
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

    assert response.status_code == 422, response.text
    state = await _state(postgres_with_real_init_schema, user, item["id"])
    assert list(state.values()) == ["open", None, 0, 0, {}, 0]


@pytest.mark.asyncio
async def test_live_implicit_create_and_approve_is_closed_and_audited(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-implicit-closed")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    try:
        with pytest.raises(HTTPException) as exc:
            await _approve(pool, user=user, item=item)
    finally:
        await pool.close()

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == IMPLICIT_SELF_APPROVAL
    state = await _state(postgres_with_real_init_schema, user, item["id"])
    assert list(state.values()) == ["open", None, 0, 0, {}, 0]
    assert await _audits(postgres_with_real_init_schema, item["id"]) == [
        {
            "user_id": user["id"],
            "action": DENIED_AUDIT_ACTION,
            "status": "denied",
            "reason": IMPLICIT_SELF_APPROVAL,
        }
    ]


@pytest.mark.asyncio
async def test_live_author_cannot_approve_own_decision(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, _checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-self-approval"
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    try:
        with pytest.raises(HTTPException) as exc:
            await _approve(
                pool, user=maker, item=item, decision_id=item["decision_id"]
            )
    finally:
        await pool.close()

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == SELF_APPROVAL
    _assert_not_approved(await _state(postgres_with_real_init_schema, maker, item["id"]))
    assert await _audits(postgres_with_real_init_schema, item["id"]) == [
        {
            "user_id": maker["id"],
            "action": DENIED_AUDIT_ACTION,
            "status": "denied",
            "reason": SELF_APPROVAL,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("system_made", [False, True])
async def test_live_distinct_approver_approves_once(
    system_made: bool,
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, checker, item = await _seed_linked(
        postgres_with_real_init_schema,
        f"p12-distinct-{'system' if system_made else 'person'}",
        system_made=system_made,
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    try:
        approval = await _approve(
            pool, user=checker, item=item, decision_id=item["decision_id"]
        )
    finally:
        await pool.close()

    assert approval.decision_id == item["decision_id"]
    state = await _state(postgres_with_real_init_schema, maker, item["id"])
    assert state["status"] == "approved"
    assert (state["decisions"], state["approved_actions"], state["lessons"]) == (1, 1, 1)
    assert await _audits(postgres_with_real_init_schema, item["id"]) == [
        {
            "user_id": checker["id"],
            "action": "control_room.approve",
            "status": "success",
            "reason": None,
        }
    ]


@pytest.mark.asyncio
async def test_live_revoked_checker_membership_is_denied(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-revoked-checker"
    )
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await admin.execute(
            "DELETE FROM user_workspace_roles WHERE user_id=$1", checker["id"]
        )
    finally:
        await admin.close()
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    try:
        with pytest.raises(HTTPException) as exc:
            await _approve(
                pool, user=checker, item=item, decision_id=item["decision_id"]
            )
    finally:
        await pool.close()

    assert exc.value.detail["code"] == APPROVER_PERMISSION
    _assert_not_approved(await _state(postgres_with_real_init_schema, maker, item["id"]))


@pytest.mark.asyncio
async def test_live_two_distinct_approvals_create_one_workflow(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-concurrent"
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=2, max_size=4)
    barrier = asyncio.Barrier(2)

    async def run():
        await barrier.wait()
        try:
            return await _approve(
                pool, user=checker, item=item, decision_id=item["decision_id"]
            )
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
    state = await _state(postgres_with_real_init_schema, maker, item["id"])
    assert state["status"] == "approved"
    assert (state["decisions"], state["approved_actions"], state["lessons"]) == (1, 1, 1)
    assert state["events"] == {"approved": 1, "lesson_recorded": 1}
    assert await _audits(postgres_with_real_init_schema, item["id"]) == [
        {
            "user_id": checker["id"],
            "action": "control_room.approve",
            "status": "success",
            "reason": None,
        }
    ]


@pytest.mark.asyncio
async def test_live_refresh_waits_for_atomic_approval(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-refresh-race"
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=2, max_size=4)
    approved = asyncio.Event()
    release = asyncio.Event()
    refresh_started = asyncio.Event()

    async def hold_after_approval(**kwargs):
        await audit_service.record_event(**kwargs)
        approved.set()
        await release.wait()

    async def refresh():
        refresh_started.set()

        async def write(conn, _tenant_id, _workspace_id):
            await persist_item_rows(
                conn,
                _rows(
                    [item],
                    maker["active_tenant_id"],
                    maker["active_workspace_id"],
                    owner=maker["id"],
                ),
                owner_scope_id=maker["id"],
            )

        await run_with_db_scope(pool, maker, write)

    approval_task = asyncio.create_task(
        _approve(
            pool,
            user=checker,
            item=item,
            decision_id=item["decision_id"],
            audit_recorder=hold_after_approval,
        )
    )
    await approved.wait()
    refresh_task = asyncio.create_task(refresh())
    await refresh_started.wait()
    await asyncio.sleep(0.1)
    assert not refresh_task.done()
    release.set()
    try:
        await asyncio.gather(approval_task, refresh_task)
    finally:
        await pool.close()

    state = await _state(postgres_with_real_init_schema, maker, item["id"])
    assert state["status"] == "approved"
    assert (state["decisions"], state["approved_actions"], state["lessons"]) == (1, 1, 1)
    assert state["events"] == {"approved": 1, "lesson_recorded": 1}
    assert await _audits(postgres_with_real_init_schema, item["id"]) == [
        {
            "user_id": checker["id"],
            "action": "control_room.approve",
            "status": "success",
            "reason": None,
        }
    ]
