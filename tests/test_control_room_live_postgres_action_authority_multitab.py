from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import business_action_handle, business_action_intents
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_tokens import handle_digest
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
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


@pytest.fixture(scope="module")
def authority_seed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=8)


def _handle(payload: dict) -> str:
    actions = [
        action
        for section in payload["sections"]
        for fact in section["facts"]
        for action in fact["actions"]
    ]
    assert len(actions) == 1
    return str(actions[0]["action_handle"])


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
        return await resolve_business_action_handle(scope.maker, handle)


async def _promote(scope: AuthorityScope, pool: asyncpg.Pool, handle: str):
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
    ):
        return await promote_action_handle(scope.maker, handle)


async def _binding_rows(seed: AuthoritySeed, scope: AuthorityScope) -> list[dict]:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        rows = await conn.fetch(
            """SELECT status, octet_length(token_digest) AS digest_bytes,
                      octet_length(binding_handle_nonce) AS nonce_bytes
                 FROM control_room_action_tokens
                WHERE workspace_id=$1::uuid AND subject_user_id=$2
                  AND item_id=$3 AND stage='action_binding'""",
            scope.workspace_id,
            scope.maker["id"],
            scope.item_id,
        )
    finally:
        await conn.close()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_sequential_gets_return_one_stable_live_handle(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "multitab-sequential")
    pool = await _pool(seed)
    try:
        first, second = await experience_gets(pool, scope, count=2, concurrent=False)
        handles = [_handle(first), _handle(second)]
        assert handles[0] == handles[1]
        resolved = [await _resolve(scope, pool, value) for value in handles]
        assert all(value.item_id == scope.item_id for value in resolved)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_concurrent_out_of_order_gets_keep_the_same_handle(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "multitab-concurrent")
    pool = await _pool(seed)
    try:
        payloads = await experience_gets(pool, scope, count=8, concurrent=True)
        handles = {_handle(payload) for payload in reversed(payloads)}
        assert len(handles) == 1
        assert (await _resolve(scope, pool, handles.pop())).item_id == scope.item_id
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_many_gets_are_bounded_without_plaintext_storage(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "multitab-bounded")
    pool = await _pool(seed)
    try:
        payloads = await experience_gets(pool, scope, count=40, concurrent=True)
        handles = {_handle(payload) for payload in payloads}
        assert len(handles) == 1
        rows = await _binding_rows(seed, scope)
        assert rows == [{"status": "active", "digest_bytes": 32, "nonce_bytes": 32}]
        handle = handles.pop()
        assert handle.encode() not in repr(rows).encode()
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_consuming_shared_handle_cannot_promote_twice(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "multitab-consume")
    await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        payloads = await experience_gets(pool, scope, count=2, concurrent=True)
        first, second = (_handle(payload) for payload in payloads)
        assert first == second
        promoted = await _promote(scope, pool, first)
        with pytest.raises(HTTPException) as replay:
            await _promote(scope, pool, second)
        assert promoted.state == "pending_approval" and replay.value.status_code == 404
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_expiry_permission_revocation_and_cross_scope_fail_closed(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "multitab-invalid")
    await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        handle = _handle(
            (await experience_gets(pool, scope, count=1, concurrent=False))[0]
        )
        for user in (seed.second.maker, seed.outsider):
            with pytest.raises(HTTPException) as denied:
                await _resolve(
                    AuthorityScope(
                        user["active_tenant_id"],
                        user["active_workspace_id"],
                        scope.item_id,
                        user,
                        scope.checker,
                        scope.item,
                    ),
                    pool,
                    handle,
                )
            assert denied.value.status_code == 404

        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            await admin.execute(
                "DELETE FROM user_workspace_roles "
                "WHERE user_id=$1 AND workspace_id=$2::uuid",
                scope.maker["id"],
                scope.workspace_id,
            )
            with pytest.raises(HTTPException) as revoked:
                await _resolve(scope, pool, handle)
            assert revoked.value.status_code == 404
            await admin.execute(
                """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
                   SELECT $1, $2::uuid, id FROM roles
                    WHERE name='workspace_admin'""",
                scope.maker["id"],
                scope.workspace_id,
            )
            await admin.execute(
                "UPDATE control_room_action_tokens "
                "SET issued_at=NOW()-INTERVAL '16 minutes', "
                "expires_at=NOW()-INTERVAL '1 minute' "
                "WHERE token_digest=$1",
                handle_digest(handle),
            )
        finally:
            await admin.close()
        with pytest.raises(HTTPException) as expired:
            await _promote(scope, pool, handle)
        assert expired.value.status_code == 404
    finally:
        await pool.close()
