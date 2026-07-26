from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.control_room.surface_snapshot import SurfaceScope
from app.services import control_room_service
from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
)
from app.services.control_room.business_action_replay import matching_action_replay
from app.services.control_room.business_action_reservation import (
    acquire_action_reservation,
    complete_action_reservation,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from app.services.db_scope import run_with_db_scope
from app.services.control_room.surface_snapshot import SurfaceSnapshot
from tests.control_room_live_action_contract import (
    ITEM_ID,
    TEMPLATE_ID,
    LiveActionScopes,
    assert_public_action_redacted,
    cleanup_live_action_scopes,
    load_live_action_item,
    seed_live_action_scopes,
)
from tests.control_room_live_template_race import (
    assert_disable_wins_before_reservation,
    assert_reservation_wins_and_blocks_later_actions,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.fixture(scope="module")
def live_action_scopes(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> LiveActionScopes:
    scopes = asyncio.run(
        seed_live_action_scopes(
            postgres_with_real_init_schema,
            omega_console_live_dsn,
        )
    )
    yield scopes
    asyncio.run(cleanup_live_action_scopes(scopes))


async def _loaded_item(pool, user):
    return await load_live_action_item(pool, user)


def _snapshot(item: dict, scopes: LiveActionScopes, index: int) -> SurfaceSnapshot:
    return SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=SurfaceScope(
            tenant_id=scopes.tenant_ids[index],
            workspace_id=scopes.workspace_ids[index],
        ),
        items=(item,),
        diagnostics=(),
        sources=(),
        installations=(),
    )


@pytest.mark.asyncio
async def test_real_postgres_experience_actions_are_scope_and_permission_bound(
    live_action_scopes: LiveActionScopes,
):
    scopes = live_action_scopes
    pool = await asyncpg.create_pool(scopes.console_dsn, min_size=1, max_size=3)
    try:
        items = [await _loaded_item(pool, scopes.users[index]) for index in range(2)]
        assert all(item is not None for item in items)
        for index, item in enumerate(items):
            with patch.object(
                control_room_service.auth,
                "pool",
                new=AsyncMock(return_value=pool),
            ):
                enabled = await load_enabled_action_template_ids(scopes.users[index])
            response = build_business_experience_v2(
                _snapshot(item, scopes, index),
                user=scopes.users[index],
                enabled_template_ids=enabled,
            ).model_dump(mode="json", exclude_none=True)
            action = response["sections"][0]["facts"][0]["actions"][0]
            assert_public_action_redacted(action)

        viewer = {**scopes.users[0], "role": "viewer"}
        viewer_payload = build_business_experience_v2(
            _snapshot(items[0], scopes, 0),
            user=viewer,
            enabled_template_ids=(),
        ).model_dump(mode="json", exclude_none=True)
        assert viewer_payload["sections"][0]["facts"][0]["actions"] == []

        cross_scope = {
            **scopes.users[0],
            "active_workspace_id": scopes.workspace_ids[1],
        }

        async def no_rows(*_args, **_kwargs):
            return []

        with patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=pool),
        ):
            with pytest.raises(HTTPException) as exc:
                await control_room_service._item_for_mutation(  # noqa: SLF001
                    ITEM_ID, cross_scope, fetcher=no_rows
                )
        assert exc.value.status_code == 404

        binding_id = items[0]["metadata"]["explicit_action_bindings"][0]["binding_id"]
        with patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=pool),
        ):
            with pytest.raises(HTTPException) as exc:
                await control_room_service.action_preview(
                    ITEM_ID,
                    scopes.users[0],
                    template_id="create_followup_task",
                    binding_id=binding_id,
                    fetcher=no_rows,
                )
        assert exc.value.status_code == 404

        conn = await asyncpg.connect(scopes.admin_dsn)
        try:
            counts = await conn.fetchrow(
                "SELECT (SELECT count(*) FROM action_runs) AS runs, "
                "(SELECT count(*) FROM action_run_events) AS events"
            )
        finally:
            await conn.close()
        assert dict(counts) == {"runs": 0, "events": 0}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_postgres_replay_cannot_cross_scope_fingerprint_or_authorization(
    live_action_scopes: LiveActionScopes,
):
    scopes = live_action_scopes
    pool = await asyncpg.create_pool(scopes.console_dsn, min_size=1, max_size=3)
    try:
        item_a = await _loaded_item(pool, scopes.users[0])
        item_b = await _loaded_item(pool, scopes.users[1])
        authorization = execution_authorization_contract(scopes.users[0])

        async def reserve(conn, tenant_id, workspace_id):
            reservation = await acquire_action_reservation(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                item=item_a,
                template_id=TEMPLATE_ID,
                adapter_name="live-test",
                operation="execute",
                actor_id=scopes.user_ids[0],
                authorization_contract=authorization,
            )
            await complete_action_reservation(
                conn,
                workspace_id=workspace_id,
                reservation_id=reservation.id,
                effective_key=reservation.effective_key,
                status="completed",
                execution_result={"ok": True},
            )
            return reservation

        reservation = await run_with_db_scope(pool, scopes.users[0], reserve)

        async def replay(conn, _tenant_id, workspace_id, item, auth_contract):
            return await matching_action_replay(
                conn,
                workspace_id=workspace_id,
                item=item,
                template_id=TEMPLATE_ID,
                operation="execute",
                authorization_contract=auth_contract,
            )

        exact = await run_with_db_scope(
            pool,
            scopes.users[0],
            lambda conn, tenant, workspace: replay(
                conn, tenant, workspace, item_a, authorization
            ),
        )
        assert exact and exact[1]["id"] == reservation.id

        cross = await run_with_db_scope(
            pool,
            scopes.users[1],
            lambda conn, tenant, workspace: replay(
                conn,
                tenant,
                workspace,
                item_b,
                execution_authorization_contract(scopes.users[1]),
            ),
        )
        changed = await run_with_db_scope(
            pool,
            scopes.users[0],
            lambda conn, tenant, workspace: replay(
                conn,
                tenant,
                workspace,
                {**item_a, "observed_value": 4},
                authorization,
            ),
        )
        substituted_auth = {**authorization, "user_id": scopes.user_ids[1]}
        wrong_auth = await run_with_db_scope(
            pool,
            scopes.users[0],
            lambda conn, tenant, workspace: replay(
                conn, tenant, workspace, item_a, substituted_auth
            ),
        )
        assert cross is changed is wrong_auth is None

        async def foreign_counts(conn, _tenant_id, _workspace_id):
            return (
                await conn.fetchval("SELECT count(*) FROM action_runs"),
                await conn.fetchval("SELECT count(*) FROM action_run_events"),
            )

        assert await run_with_db_scope(pool, scopes.users[1], foreign_counts) == (0, 0)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_postgres_template_disable_wins_before_reservation(
    live_action_scopes: LiveActionScopes,
):
    await assert_disable_wins_before_reservation(live_action_scopes)


@pytest.mark.asyncio
async def test_real_postgres_reservation_wins_and_blocks_later_actions(
    live_action_scopes: LiveActionScopes,
):
    await assert_reservation_wins_and_blocks_later_actions(live_action_scopes)
