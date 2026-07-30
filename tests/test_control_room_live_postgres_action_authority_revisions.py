from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import business_action_handle, business_action_intents
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_preview_capability import (
    lock_contextual_preview_authority,
)
from app.services.db_scope import run_with_db_scope
from app.services.permission_roles import ROLE_DEFINITIONS, ROLE_PERMISSIONS
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import issue_live_binding
from tests.control_room_action_authority_http import experience_gets
from tests.control_room_action_authority_live import (
    AuthorityScope,
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
    snapshot,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)

OWNER_WRITER = "control_room_revision_owner_writer"


@pytest.fixture(scope="module")
def authority_seed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)


def _handle(payload: dict) -> str:
    actions = [
        action
        for section in payload["sections"]
        for fact in section["facts"]
        for action in fact["actions"]
    ]
    assert len(actions) == 1
    return str(actions[0]["action_handle"])


async def _set_role(seed: AuthoritySeed, scope: AuthorityScope, role: str | None):
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM user_workspace_roles WHERE user_id=$1 AND workspace_id=$2::uuid",
            scope.maker["id"],
            scope.workspace_id,
        )
        if role:
            await conn.execute(
                """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
                   SELECT $1, $2::uuid, id FROM roles WHERE name=$3""",
                scope.maker["id"],
                scope.workspace_id,
                role,
            )
    finally:
        await conn.close()


async def _resolve(scope: AuthorityScope, pool: asyncpg.Pool, handle: str):
    with (
        patch.object(
            business_action_handle.auth, "pool", new=AsyncMock(return_value=pool)
        ),
        patch.object(
            business_action_handle,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
        patch.object(
            business_action_handle,
            "load_enabled_action_template_ids",
            new=AsyncMock(return_value=frozenset({"create_followup_task"})),
        ),
    ):
        return await business_action_handle.resolve_business_action_handle(
            scope.maker, handle
        )


async def _revoke_regrant(seed: AuthoritySeed, scope: AuthorityScope):
    await _set_role(seed, scope, None)
    await asyncio.sleep(0.002)
    await _set_role(seed, scope, "workspace_admin")


@pytest.mark.asyncio
async def test_regrant_rotates_the_single_binding_slot(authority_seed: AuthoritySeed):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "revision-rotate")
    pool = await _pool(seed)
    try:
        h1 = _handle((await experience_gets(pool, scope, count=1, concurrent=False))[0])
        await _revoke_regrant(seed, scope)
        h2 = _handle((await experience_gets(pool, scope, count=1, concurrent=False))[0])
        assert h2 != h1
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            count = await conn.fetchval(
                "SELECT count(*) FROM control_room_action_tokens "
                "WHERE workspace_id=$1::uuid AND subject_user_id=$2 "
                "AND item_id=$3 AND stage='action_binding'",
                scope.workspace_id,
                scope.maker["id"],
                scope.item_id,
            )
        finally:
            await conn.close()
        assert count == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_revoked_handle_never_revives_after_same_role_regrant(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "revision-dead")
    pool = await _pool(seed)
    try:
        h1 = _handle((await experience_gets(pool, scope, count=1, concurrent=False))[0])
        await _set_role(seed, scope, None)
        with pytest.raises(HTTPException) as revoked:
            await _resolve(scope, pool, h1)
        assert revoked.value.status_code == 404
        await asyncio.sleep(0.002)
        await _set_role(seed, scope, "workspace_admin")
        with pytest.raises(HTTPException) as still_revoked:
            await _resolve(scope, pool, h1)
        assert still_revoked.value.status_code == 404
    finally:
        await pool.close()


async def _foreign_owner_scope(
    seed: AuthoritySeed, marker: str, monkeypatch: pytest.MonkeyPatch
) -> AuthorityScope:
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        OWNER_WRITER,
        {"workspace.access", "datasets.read", "control_room.write"},
    )
    monkeypatch.setitem(
        ROLE_DEFINITIONS,
        OWNER_WRITER,
        {
            "label": "Revision owner writer",
            "description": "Test-only owner-scoped writer",
            "assignable": False,
            "builtin": False,
            "legacy": False,
        },
    )
    scope = await seed_authority_item(seed, seed.first, marker)
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            "INSERT INTO roles(name, description) VALUES ($1, $2) "
            "ON CONFLICT (name) DO NOTHING",
            OWNER_WRITER,
            "Test-only owner-scoped writer",
        )
        await conn.execute(
            "UPDATE control_room_items SET owner_user_id=$3 "
            "WHERE workspace_id=$1::uuid AND item_id=$2",
            scope.workspace_id,
            scope.item_id,
            scope.checker["id"],
        )
    finally:
        await conn.close()
    return replace(scope, item={**scope.item, "owner_user_id": scope.checker["id"]})


@pytest.mark.asyncio
async def test_stale_admin_claim_cannot_emit_for_foreign_owner(
    authority_seed: AuthoritySeed, monkeypatch: pytest.MonkeyPatch
):
    seed = authority_seed
    scope = await _foreign_owner_scope(seed, "revision-emit", monkeypatch)
    await _set_role(seed, scope, OWNER_WRITER)
    pool = await _pool(seed)
    try:
        payload = (await experience_gets(pool, scope, count=1, concurrent=False))[0]
        assert all(
            fact["actions"] == []
            for section in payload["sections"]
            for fact in section["facts"]
        )
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            count = await conn.fetchval(
                "SELECT count(*) FROM control_room_action_tokens "
                "WHERE workspace_id=$1::uuid AND subject_user_id=$2 "
                "AND item_id=$3",
                scope.workspace_id,
                scope.maker["id"],
                scope.item_id,
            )
        finally:
            await conn.close()
        assert count == 0
    finally:
        await _set_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
async def test_preview_lock_rejects_db_demotion_before_first_dml(
    authority_seed: AuthoritySeed, monkeypatch: pytest.MonkeyPatch
):
    seed = authority_seed
    scope = await _foreign_owner_scope(seed, "revision-preview", monkeypatch)
    pool = await _pool(seed)
    try:
        handle = (await issue_live_binding(seed, pool, scope)).action_handle
        await _resolve(scope, pool, handle)
        await _set_role(seed, scope, OWNER_WRITER)

        async def lock(conn, _tenant, _workspace):
            await lock_contextual_preview_authority(
                conn,
                item=scope.item,
                user=scope.maker,
                binding_id=handle,
            )

        with pytest.raises(HTTPException) as denied:
            await run_with_db_scope(pool, scope.maker, lock)
        assert denied.value.status_code in {404, 409}
    finally:
        await _set_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
async def test_promotion_uses_db_owner_scope_not_stale_session_role(
    authority_seed: AuthoritySeed, monkeypatch: pytest.MonkeyPatch
):
    seed = authority_seed
    scope = await _foreign_owner_scope(seed, "revision-promote", monkeypatch)
    await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        handle = (await issue_live_binding(seed, pool, scope)).action_handle
        await _set_role(seed, scope, OWNER_WRITER)
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_intents,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(scope)),
            ),
            pytest.raises(HTTPException) as denied,
        ):
            await promote_action_handle(scope.maker, handle)
        assert denied.value.status_code in {403, 404}
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            counts = await conn.fetchrow(
                "SELECT (SELECT count(*) FROM control_room_action_intents "
                "WHERE workspace_id=$1::uuid AND item_id=$2) AS intents, "
                "(SELECT count(*) FROM control_room_action_intent_events e "
                "JOIN control_room_action_intents i ON i.id=e.intent_id "
                "WHERE i.workspace_id=$1::uuid AND i.item_id=$2) AS events, "
                "(SELECT count(*) FROM control_room_action_tokens t "
                "WHERE t.workspace_id=$1::uuid AND t.item_id=$2 "
                "AND t.stage='workflow') AS workflows",
                scope.workspace_id,
                scope.item_id,
            )
        finally:
            await conn.close()
        assert dict(counts) == {"intents": 0, "events": 0, "workflows": 0}
    finally:
        await _set_role(seed, scope, "workspace_admin")
        await pool.close()
