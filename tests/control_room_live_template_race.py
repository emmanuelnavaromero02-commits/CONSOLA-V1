from __future__ import annotations

import asyncio

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_catalog import (
    require_enabled_action_template,
)
from app.services.control_room.business_action_reservation import (
    acquire_action_reservation,
    complete_action_reservation,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_live_action_contract import (
    ITEM_ID,
    TEMPLATE_ID,
    LiveActionScopes,
)


async def _reset(scopes: LiveActionScopes, *, enabled: bool) -> None:
    conn = await asyncpg.connect(scopes.admin_dsn)
    try:
        await conn.execute("DELETE FROM action_run_events WHERE item_id=$1", ITEM_ID)
        await conn.execute("DELETE FROM action_runs WHERE item_id=$1", ITEM_ID)
        await conn.execute(
            "UPDATE control_room_action_templates SET enabled=$2 "
            "WHERE template_id=$1",
            TEMPLATE_ID,
            enabled,
        )
    finally:
        await conn.close()


async def _scope(conn, scopes: LiveActionScopes) -> None:
    await conn.execute(
        SET_SCOPE_SQL,
        scopes.tenant_ids[0],
        scopes.workspace_ids[0],
    )


async def assert_disable_wins_before_reservation(scopes: LiveActionScopes) -> None:
    await _reset(scopes, enabled=True)
    reservation_conn = await asyncpg.connect(scopes.console_dsn)
    disable_conn = await asyncpg.connect(scopes.admin_dsn)
    observer = await asyncpg.connect(scopes.admin_dsn)
    disabled = asyncio.Event()
    release = asyncio.Event()
    attempt_started = asyncio.Event()
    reservation_pid = await reservation_conn.fetchval("SELECT pg_backend_pid()")

    async def disable():
        async with disable_conn.transaction():
            await disable_conn.execute(
                "UPDATE control_room_action_templates SET enabled=FALSE "
                "WHERE template_id=$1",
                TEMPLATE_ID,
            )
            disabled.set()
            await release.wait()

    async def reserve():
        await disabled.wait()
        async with reservation_conn.transaction():
            await _scope(reservation_conn, scopes)
            attempt_started.set()
            await require_enabled_action_template(reservation_conn, TEMPLATE_ID)
            raise AssertionError("disabled template reached reservation")

    disable_task = asyncio.create_task(disable())
    reserve_task = asyncio.create_task(reserve())
    try:
        await asyncio.wait_for(attempt_started.wait(), timeout=2)
        blocked = False
        for _ in range(100):
            blocked = bool(
                await observer.fetchval(
                    "SELECT cardinality(pg_blocking_pids($1)) > 0",
                    reservation_pid,
                )
            )
            if blocked:
                break
            await asyncio.sleep(0.01)
        assert blocked, "reservation did not wait for the disable lock"
        release.set()
        await asyncio.wait_for(disable_task, timeout=3)
        with pytest.raises(HTTPException) as exc:
            await asyncio.wait_for(reserve_task, timeout=3)
        assert exc.value.status_code == 404
        counts = await observer.fetchrow(
            "SELECT (SELECT count(*) FROM action_runs WHERE item_id=$1) AS runs, "
            "(SELECT count(*) FROM action_run_events WHERE item_id=$1) AS events",
            ITEM_ID,
        )
        assert dict(counts) == {"runs": 0, "events": 0}
    finally:
        release.set()
        for task in (disable_task, reserve_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(disable_task, reserve_task, return_exceptions=True)
        await reservation_conn.close()
        await disable_conn.close()
        await observer.close()
        await _reset(scopes, enabled=True)


async def assert_reservation_wins_and_blocks_later_actions(
    scopes: LiveActionScopes,
) -> None:
    await _reset(scopes, enabled=True)
    reservation_conn = await asyncpg.connect(scopes.console_dsn)
    disable_conn = await asyncpg.connect(scopes.admin_dsn)
    observer = await asyncpg.connect(scopes.admin_dsn)
    locked = asyncio.Event()
    release = asyncio.Event()
    update_started = asyncio.Event()
    authorization = execution_authorization_contract(scopes.users[0])
    disable_pid = await disable_conn.fetchval("SELECT pg_backend_pid()")

    async def reserve():
        async with reservation_conn.transaction():
            await _scope(reservation_conn, scopes)
            await require_enabled_action_template(reservation_conn, TEMPLATE_ID)
            locked.set()
            await release.wait()
            return await acquire_action_reservation(
                reservation_conn,
                tenant_id=scopes.tenant_ids[0],
                workspace_id=scopes.workspace_ids[0],
                item=scopes.items[0],
                template_id=TEMPLATE_ID,
                adapter_name="live-race",
                operation="execute",
                actor_id=scopes.user_ids[0],
                authorization_contract=authorization,
            )

    async def disable():
        await locked.wait()
        async with disable_conn.transaction():
            update_started.set()
            await disable_conn.execute(
                "UPDATE control_room_action_templates SET enabled=FALSE "
                "WHERE template_id=$1",
                TEMPLATE_ID,
            )

    reserve_task = asyncio.create_task(reserve())
    disable_task = asyncio.create_task(disable())
    try:
        await asyncio.wait_for(update_started.wait(), timeout=2)
        blocked = False
        for _ in range(100):
            blocked = bool(
                await observer.fetchval(
                    "SELECT cardinality(pg_blocking_pids($1)) > 0", disable_pid
                )
            )
            if blocked:
                break
            await asyncio.sleep(0.01)
        assert blocked, "template disable did not wait for the reservation lock"

        release.set()
        reservation = await asyncio.wait_for(reserve_task, timeout=3)
        await asyncio.wait_for(disable_task, timeout=3)

        async with reservation_conn.transaction():
            await _scope(reservation_conn, scopes)
            completed = await complete_action_reservation(
                reservation_conn,
                workspace_id=scopes.workspace_ids[0],
                reservation_id=reservation.id,
                effective_key=reservation.effective_key,
                status="completed",
                execution_result={"ok": True},
            )
            assert completed["status"] == "completed"
            with pytest.raises(HTTPException) as exc:
                await require_enabled_action_template(reservation_conn, TEMPLATE_ID)
            assert exc.value.status_code == 404

        counts = await observer.fetchrow(
            "SELECT (SELECT count(*) FROM action_runs WHERE item_id=$1) AS runs, "
            "(SELECT count(*) FROM action_run_events WHERE item_id=$1) AS events",
            ITEM_ID,
        )
        assert dict(counts) == {"runs": 1, "events": 0}
    finally:
        release.set()
        for task in (reserve_task, disable_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(reserve_task, disable_task, return_exceptions=True)
        await reservation_conn.close()
        await disable_conn.close()
        await observer.close()
        await _reset(scopes, enabled=True)


__all__ = (
    "assert_disable_wins_before_reservation",
    "assert_reservation_wins_and_blocks_later_actions",
)
