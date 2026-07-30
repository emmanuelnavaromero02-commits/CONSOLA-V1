from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services import auth
from app.routers import control_room
from app.services.control_room import business_action_handle
from app.services.control_room import business_action_intents
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_preview_capability import (
    lock_contextual_preview_authority,
)
from app.services.db_scope import run_with_db_scope
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import issue_live_binding
from tests.control_room_action_authority_http import _app, experience_gets
from tests.control_room_action_authority_live import (
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
    snapshot,
)
from tests.test_control_room_live_postgres_action_authority_revisions import (
    OWNER_WRITER,
    _foreign_owner_scope,
    _handle,
    _pool,
    _resolve,
    _set_role,
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


@pytest.mark.asyncio
async def test_owner_scoped_demotion_blocks_resolve_with_stale_admin_claim(
    authority_seed: AuthoritySeed, monkeypatch: pytest.MonkeyPatch
):
    seed = authority_seed
    scope = await _foreign_owner_scope(seed, "revision-resolve", monkeypatch)
    pool = await _pool(seed)
    try:
        handle = (await issue_live_binding(seed, pool, scope)).action_handle
        await _set_role(seed, scope, OWNER_WRITER)
        with pytest.raises(HTTPException) as denied:
            await _resolve(scope, pool, handle)
        assert denied.value.status_code == 404
    finally:
        await _set_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
async def test_http_preview_denies_stale_admin_claim_before_service_dml(
    authority_seed: AuthoritySeed, monkeypatch: pytest.MonkeyPatch
):
    seed = authority_seed
    scope = await _foreign_owner_scope(seed, "revision-http", monkeypatch)
    pool = await _pool(seed)
    try:
        handle = (await issue_live_binding(seed, pool, scope)).action_handle
        await _set_role(seed, scope, OWNER_WRITER)
        app = _app(scope.maker)

        async def _csrf():
            return None

        app.dependency_overrides[control_room.require_csrf] = _csrf
        with (
            patch.object(
                business_action_handle.auth,
                "pool",
                new=AsyncMock(return_value=pool),
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
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://authority.test",
            ) as client:
                response = await client.post(
                    "/api/control-room/actions/preview",
                    json={"action_handle": handle},
                )
        assert response.status_code == 404
        assert handle not in response.text
    finally:
        await _set_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
async def test_old_revision_blocks_preview_lock_and_promotion_after_regrant(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "revision-all-paths")
    await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        handle = (await issue_live_binding(seed, pool, scope)).action_handle
        await _resolve(scope, pool, handle)
        await _set_role(seed, scope, None)
        await asyncio.sleep(0.002)
        await _set_role(seed, scope, "workspace_admin")

        async def lock(conn, _tenant, _workspace):
            await lock_contextual_preview_authority(
                conn,
                item=scope.item,
                user=scope.maker,
                binding_id=handle,
            )

        with pytest.raises(HTTPException) as preview_denied:
            await run_with_db_scope(pool, scope.maker, lock)
        assert preview_denied.value.status_code == 409
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_intents,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(scope)),
            ),
            pytest.raises(HTTPException) as promotion_denied,
        ):
            await promote_action_handle(scope.maker, handle)
        assert promotion_denied.value.status_code == 404
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_current_db_admin_outweighs_stale_owner_scoped_session_claim(
    authority_seed: AuthoritySeed, monkeypatch: pytest.MonkeyPatch
):
    seed = authority_seed
    scope = await _foreign_owner_scope(seed, "revision-db-wins", monkeypatch)
    stale = replace(
        scope,
        maker={**scope.maker, "role": "user", "workspace_role": OWNER_WRITER},
    )
    pool = await _pool(seed)
    try:
        payload = (await experience_gets(pool, stale, count=1, concurrent=False))[0]
        assert len(_handle(payload)) == 64
    finally:
        await pool.close()
