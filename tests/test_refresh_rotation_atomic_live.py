"""Real-Postgres concurrency proof for single-use refresh rotation."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest

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
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "refresh-race-signing-key-" + "x" * 32
        )
        monkeypatch.setenv("JWT_ALGORITHM", "HS256")
        monkeypatch.setenv(
            "INTERNAL_API_KEY", "refresh-race-internal-key-" + "y" * 32
        )

        # The root test harness deliberately evicts ``app.*`` between tests.
        # Patch the exact auth module retained by the production route rather
        # than the collection-time module reference above.
        from app import main as console_main

        route_auth = console_main._auth
        monkeypatch.setattr(route_auth, "pool", real_pool)
        attempts = 16
        callers_ready = 0
        callers_lock = asyncio.Lock()
        release_callers = asyncio.Event()
        real_rotate = route_auth.rotate_refresh_token

        async def synchronized_rotate(token: str | None):
            nonlocal callers_ready
            async with callers_lock:
                callers_ready += 1
                if callers_ready == attempts:
                    release_callers.set()
            await asyncio.wait_for(release_callers.wait(), timeout=10)
            return await real_rotate(token)

        monkeypatch.setattr(
            route_auth, "rotate_refresh_token", synchronized_rotate
        )

        # Exercise the production route, including CSRF, cookie extraction and
        # the invalid-token-to-401 mapping.
        transport = httpx.ASGITransport(app=console_main.app)
        csrf_token = "refresh-race-csrf"
        headers = {
            "X-CSRF-Token": csrf_token,
            "Cookie": (
                f"{route_auth.REFRESH_COOKIE_NAME}={raw_refresh}; "
                f"csrf_token={csrf_token}"
            ),
        }
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fseg.invalid"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/auth/refresh",
                        headers=headers,
                    )
                    for _ in range(attempts)
                )
            )
        assert callers_ready == attempts
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
