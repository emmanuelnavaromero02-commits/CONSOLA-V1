from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.services import auth
from app.services.control_room import business_action_revalidation
from app.services.control_room.business_action_transitions import (
    claim_intent_for_approval,
)
from tests.control_room_action_authority_flow import promote_live_intent
from tests.control_room_action_authority_live import (
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


EVENT = "control_room_action_authority_revalidation_operational_failure"
SAFE_DETAIL = "action authority is temporarily unavailable"


@pytest.fixture(scope="module")
def authority_seed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)


async def _state(seed: AuthoritySeed, intent_id: str) -> dict:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        intent = await conn.fetchrow(
            "SELECT state, state_version, result_code FROM control_room_action_intents "
            "WHERE id=$1::uuid",
            intent_id,
        )
        events = await conn.fetchval(
            "SELECT count(*) FROM control_room_action_intent_events "
            "WHERE intent_id=$1::uuid",
            intent_id,
        )
        tokens = await conn.fetch(
            "SELECT stage, status FROM control_room_action_tokens "
            "WHERE intent_id=$1::uuid ORDER BY stage",
            intent_id,
        )
    finally:
        await conn.close()
    return {
        "intent": dict(intent),
        "events": int(events),
        "tokens": [dict(row) for row in tokens],
    }


def _failure(kind: str) -> Exception:
    raw = "SELECT secret FROM /srv/private handle=" + "a" * 64
    return RuntimeError(raw) if kind == "runtime" else asyncpg.PostgresError(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("runtime", "postgres"))
async def test_operational_revalidation_rolls_back_without_stale(
    authority_seed: AuthoritySeed,
    caplog: pytest.LogCaptureFixture,
    kind: str,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, f"operational-{kind}")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        before = await _state(seed, promoted.intent_id)
        caplog.set_level(logging.ERROR)
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_revalidation,
                "require_enabled_action_template",
                new=AsyncMock(side_effect=_failure(kind)),
            ),
            pytest.raises(HTTPException) as unavailable,
        ):
            await claim_intent_for_approval(scope.checker, promoted.intent_id)
        assert unavailable.value.status_code == 503
        assert unavailable.value.detail == SAFE_DETAIL
        assert await _state(seed, promoted.intent_id) == before
        records = [record for record in caplog.records if record.message == EVENT]
        assert len(records) == 1
        assert records[0].exc_info is None
        assert records[0].event == EVENT
        assert records[0].component == "control_room_action_authority"
        assert records[0].outcome == "transaction_rolled_back"
        assert not any(
            value in records[0].getMessage()
            for value in ("SELECT", "/srv", "handle", "a" * 64)
        )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_operational_revalidation_has_safe_http_503(
    authority_seed: AuthoritySeed,
    caplog: pytest.LogCaptureFixture,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "operational-http")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        before = await _state(seed, promoted.intent_id)
        app = FastAPI()

        @app.post("/authority/{intent_id}")
        async def _claim(intent_id: str):
            return await claim_intent_for_approval(scope.checker, intent_id)

        caplog.set_level(logging.ERROR)
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_revalidation,
                "require_authority_dry_run",
                new=AsyncMock(side_effect=RuntimeError("raw /srv/private SQL")),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://authority.test",
            ) as client:
                response = await client.post(f"/authority/{promoted.intent_id}")
        assert response.status_code == 503
        assert response.json() == {"detail": SAFE_DETAIL}
        assert "/srv" not in response.text and "SQL" not in response.text
        assert await _state(seed, promoted.intent_id) == before
        assert sum(record.message == EVENT for record in caplog.records) == 1
    finally:
        await pool.close()
