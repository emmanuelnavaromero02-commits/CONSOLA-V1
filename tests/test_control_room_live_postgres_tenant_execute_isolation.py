from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Request

from app.routers import control_room as control_room_router
from app.services import control_room_service
from app.services.control_room import state as control_room_state
from tests.control_room_live_action_contract import ITEM_ID, LiveActionScopes
from tests.test_control_room_live_postgres_action_scope import live_action_scopes
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


_FIXTURE_IMPORTS = (omega_console_live_dsn, postgres_with_real_init_schema)


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


async def _seed_foreign_item(scopes: LiveActionScopes, item_id: str) -> None:
    conn = await asyncpg.connect(scopes.admin_dsn)
    try:
        await conn.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, owner_user_id, item_id,
                cartridge_id, domain, source_dataset, item_kind, title,
                severity, status, decision_id, entity_kind, entity_id,
                entity_label, anomaly_type, metadata, execution_status
            )
            SELECT tenant_id, workspace_id, owner_user_id, $3,
                   cartridge_id, domain, source_dataset, item_kind, title,
                   severity, status, decision_id, entity_kind, entity_id,
                   entity_label, anomaly_type, metadata, execution_status
              FROM control_room_items
             WHERE workspace_id=$1::uuid AND item_id=$2
            """,
            scopes.workspace_ids[1],
            ITEM_ID,
            item_id,
        )
    finally:
        await conn.close()


async def _foreign_state(scopes: LiveActionScopes, item_id: str) -> dict:
    conn = await asyncpg.connect(scopes.admin_dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT status, decision_id, execution_status, metadata
              FROM control_room_items
             WHERE workspace_id=$1::uuid AND item_id=$2
            """,
            scopes.workspace_ids[1],
            item_id,
        )
        runs = await conn.fetchval(
            "SELECT count(*) FROM action_runs "
            "WHERE workspace_id=$1::uuid AND item_id=$2",
            scopes.workspace_ids[1],
            item_id,
        )
        events = await conn.fetchval(
            "SELECT count(*) FROM control_room_item_events "
            "WHERE workspace_id=$1::uuid AND item_id=$2",
            scopes.workspace_ids[1],
            item_id,
        )
        return {"item": dict(row), "runs": runs, "events": events}
    finally:
        await conn.close()


async def _delete_foreign_item(scopes: LiveActionScopes, item_id: str) -> None:
    conn = await asyncpg.connect(scopes.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM control_room_items WHERE workspace_id=$1::uuid AND item_id=$2",
            scopes.workspace_ids[1],
            item_id,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_post_execute_cannot_mutate_another_tenant(
    live_action_scopes: LiveActionScopes,
) -> None:
    scopes = live_action_scopes
    foreign_item_id = f"foreign-execute-{uuid4().hex}"
    await _seed_foreign_item(scopes, foreign_item_id)
    before = await _foreign_state(scopes, foreign_item_id)
    pool = await asyncpg.create_pool(scopes.console_dsn, min_size=1, max_size=2)
    try:
        with (
            patch.object(
                control_room_service.auth,
                "pool",
                new=AsyncMock(return_value=pool),
            ),
            patch.object(
                control_room_state,
                "_collect_items",
                new=AsyncMock(return_value={"items": [], "diagnostics": []}),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(scopes.users[0])),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    f"/api/control-room/items/{foreign_item_id}/execute",
                    headers={"authorization": "Bearer test"},
                    json={
                        "template_id": "request_owner_review",
                        "binding_id": "0" * 64,
                        "confirm_execute": True,
                        "idempotency_key": f"foreign-{uuid4().hex}",
                    },
                )
        assert response.status_code == 410
        assert response.content == b""
        assert await _foreign_state(scopes, foreign_item_id) == before
    finally:
        await pool.close()
        await _delete_foreign_item(scopes, foreign_item_id)
