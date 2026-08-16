"""Real-Postgres concurrency proof for single-use refresh rotation."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services import auth
from tests.test_identity_session_boundary_live import _seed_identity
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_concurrent_refresh_consumes_exactly_once(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = await _seed_identity(postgres_with_real_init_schema)
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=12)
    raw_refresh = secrets.token_urlsafe(32)
    old_hash = auth.hash_refresh_token(raw_refresh)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    admin = await asyncpg.connect(postgres_with_real_init_schema)

    async def real_pool() -> asyncpg.Pool:
        return pool

    try:
        async with pool.acquire() as conn:
            await conn.fetchval(
                "SELECT omega_auth_create_refresh_token($1, $2, $3)",
                scope["user_a"],
                old_hash,
                expires,
            )
        monkeypatch.setattr(auth, "pool", real_pool)

        race_app = FastAPI()

        @race_app.post("/auth/refresh")
        async def refresh(request: Request):
            rotated = await auth.rotate_refresh_token(
                request.cookies.get(auth.REFRESH_COOKIE_NAME)
            )
            if not rotated:
                return JSONResponse({"detail": "invalid refresh token"}, status_code=401)
            return JSONResponse({"token_type": "bearer"}, status_code=200)

        attempts = 16
        transport = httpx.ASGITransport(app=race_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fseg.invalid"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/auth/refresh",
                        cookies={auth.REFRESH_COOKIE_NAME: raw_refresh},
                    )
                    for _ in range(attempts)
                )
            )
        statuses = [response.status_code for response in responses]
        assert statuses.count(200) == 1
        assert statuses.count(401) == attempts - 1

        state = await admin.fetchrow(
            """SELECT count(*)::int AS total,
                      count(*) FILTER (WHERE revoked_at IS NULL)::int AS active,
                      count(*) FILTER (WHERE token_hash = $1 AND revoked_at IS NOT NULL)::int AS old_consumed
                 FROM refresh_tokens WHERE user_id = $2""",
            old_hash,
            scope["user_a"],
        )
        assert dict(state) == {"total": 2, "active": 1, "old_consumed": 1}
        assert await admin.fetchval(
            "SELECT count(*) FROM refresh_tokens WHERE token_hash = $1", raw_refresh
        ) == 0
    finally:
        await admin.close()
        await pool.close()
