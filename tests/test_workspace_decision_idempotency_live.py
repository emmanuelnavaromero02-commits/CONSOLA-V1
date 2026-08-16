"""Isolated real-Postgres race proof for Workspace decision actions."""
from __future__ import annotations

import asyncio
import importlib
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


_WORKSPACE_PASSWORD = "test_omega_workspace_password"


def _workspace_dsn(admin_dsn: str) -> str:
    return admin_dsn.replace(
        "postgres:test_postgres_password",
        f"omega_workspace:{_WORKSPACE_PASSWORD}",
    )


def _load_workspace_main(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "workspace"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "INTERNAL_API_KEY",
        "workspace_live_fseg_key_with_more_than_32_chars",
    )
    return importlib.import_module("app.main")


def _load_console_main(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "console"))
    console_env = {
        "APP_ENV": "test",
        "INTERNAL_API_KEY": "console_live_fseg_key_with_more_than_32_chars",
        "JWT_SECRET_KEY": "console_live_fseg_jwt_with_more_than_32_chars",
        "MINIO_SECRET_KEY": "synthetic-minio-canary",
        "POSTGRES_PASSWORD": "synthetic-postgres-canary",
        "ADMIN_EMAIL": "admin@invalid.example",
        "ADMIN_PASSWORD": "synthetic-admin-canary",
    }
    for name, value in console_env.items():
        monkeypatch.setenv(name, value)
    return importlib.import_module("app.main")


async def _seed(admin_dsn: str) -> dict:
    suffix = uuid.uuid4().hex
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_id = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            f"fseg-idempotency-{suffix}",
            f"fseg-idempotency-{suffix}",
        )
        workspace_id = await conn.fetchval(
            "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
            tenant_id,
            f"FSEG Idempotency {suffix}",
        )
        user_id = await conn.fetchval(
            """INSERT INTO users (
                   email, name, password_hash, role, is_active, tenant_id
               ) VALUES ($1, 'FSEG writer', 'synthetic-not-a-secret',
                         'workspace_admin', TRUE, $2)
               RETURNING id""",
            f"fseg-writer-{suffix}@invalid.example",
            tenant_id,
        )
        role_id = await conn.fetchval(
            "SELECT id FROM roles WHERE name = 'workspace_admin'"
        )
        await conn.execute(
            """INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
               VALUES ($1, $2, $3)""",
            user_id,
            workspace_id,
            role_id,
        )
        decision_id = await conn.fetchval(
            """INSERT INTO decisions (
                   title, description, created_by_id, visibility, workspace_id
               ) VALUES ('synthetic-race-canary', '', $1, 'private', $2)
               RETURNING id""",
            user_id,
            workspace_id,
        )
        return {
            "tenant_id": str(tenant_id),
            "workspace_id": str(workspace_id),
            "user_id": user_id,
            "email": f"fseg-writer-{suffix}@invalid.example",
            "decision_id": decision_id,
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_ten_simultaneous_posts_same_key_create_exactly_one_action(
    postgres_with_real_init_schema: str,
    monkeypatch,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    main = _load_workspace_main(monkeypatch)
    pool = await asyncpg.create_pool(
        _workspace_dsn(postgres_with_real_init_schema),
        min_size=2,
        max_size=12,
    )
    monkeypatch.setattr(main, "_PG_POOL", pool)
    user = {
        "id": scope["user_id"],
        "email": scope["email"],
        "role": "workspace_admin",
        "workspace_role": "workspace_admin",
        "active_tenant_id": scope["tenant_id"],
        "active_workspace_id": scope["workspace_id"],
    }
    canary_key = f"synthetic-race-{uuid.uuid4().hex}"

    async def post_once():
        request = SimpleNamespace(
            state=SimpleNamespace(user=dict(user)),
            headers={"Idempotency-Key": canary_key},
        )
        return await main.api_decisions_add_action(
            request,
            scope["decision_id"],
            {"action_text": "synthetic-race-action"},
        )

    try:
        results = await asyncio.gather(*(post_once() for _ in range(10)))
    finally:
        await pool.close()
        main._PG_POOL = None

    action_ids = {result["id"] for result in results}
    assert len(action_ids) == 1

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        action_count = await check.fetchval(
            "SELECT COUNT(*) FROM decision_actions WHERE decision_id = $1",
            scope["decision_id"],
        )
        ledger = await check.fetch(
            """SELECT actor_user_id, workspace_id::text AS workspace_id,
                      operation, idempotency_key_hash, status, action_id
                 FROM workspace_decision_idempotency
                WHERE actor_user_id = $1 AND workspace_id = $2""",
            scope["user_id"],
            scope["workspace_id"],
        )
    finally:
        await check.close()

    assert action_count == 1
    assert len(ledger) == 1
    assert ledger[0]["operation"] == f"decision.action.create:{scope['decision_id']}"
    assert ledger[0]["status"] == "completed"
    assert ledger[0]["action_id"] in action_ids
    assert ledger[0]["idempotency_key_hash"] != canary_key
    assert len(ledger[0]["idempotency_key_hash"]) == 64


@pytest.mark.asyncio
async def test_console_ten_simultaneous_posts_same_key_create_exactly_one_action(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    main = _load_console_main(monkeypatch)
    pool = await asyncpg.create_pool(
        omega_console_live_dsn,
        min_size=2,
        max_size=12,
    )
    monkeypatch.setattr(main, "_DEC_POOL", pool)
    user = {
        "id": scope["user_id"],
        "email": scope["email"],
        "role": "workspace_admin",
        "workspace_role": "workspace_admin",
        "active_tenant_id": scope["tenant_id"],
        "active_workspace_id": scope["workspace_id"],
    }
    canary_key = f"synthetic-console-race-{uuid.uuid4().hex}"

    async def post_once():
        return await main.api_decisions_add_action(
            decision_id=scope["decision_id"],
            body={"action_text": "synthetic-console-race-action"},
            request=SimpleNamespace(headers={"Idempotency-Key": canary_key}),
            user=dict(user),
        )

    try:
        results = await asyncio.gather(*(post_once() for _ in range(10)))
    finally:
        await pool.close()
        main._DEC_POOL = None

    action_ids = {result["id"] for result in results}
    assert len(action_ids) == 1

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        action_count = await check.fetchval(
            "SELECT COUNT(*) FROM decision_actions WHERE decision_id = $1",
            scope["decision_id"],
        )
        ledger = await check.fetch(
            """SELECT actor_user_id, workspace_id::text AS workspace_id,
                      operation, idempotency_key_hash, status, action_id
                 FROM workspace_decision_idempotency
                WHERE actor_user_id = $1 AND workspace_id = $2""",
            scope["user_id"],
            scope["workspace_id"],
        )
    finally:
        await check.close()

    assert action_count == 1
    assert len(ledger) == 1
    assert ledger[0]["operation"] == f"decision.action.create:{scope['decision_id']}"
    assert ledger[0]["status"] == "completed"
    assert ledger[0]["action_id"] in action_ids
    assert ledger[0]["idempotency_key_hash"] != canary_key
    assert len(ledger[0]["idempotency_key_hash"]) == 64


@pytest.mark.asyncio
async def test_workspace_admin_can_delete_scoped_decision_with_service_role(
    postgres_with_real_init_schema: str,
    monkeypatch,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    main = _load_workspace_main(monkeypatch)
    pool = await asyncpg.create_pool(
        _workspace_dsn(postgres_with_real_init_schema),
        min_size=1,
        max_size=3,
    )
    monkeypatch.setattr(main, "_PG_POOL", pool)
    request = SimpleNamespace(
        state=SimpleNamespace(
            user={
                "id": scope["user_id"],
                "email": scope["email"],
                "role": "workspace_admin",
                "workspace_role": "workspace_admin",
                "active_tenant_id": scope["tenant_id"],
                "active_workspace_id": scope["workspace_id"],
            }
        )
    )

    try:
        response = await main.api_decisions_delete(request, scope["decision_id"])
    finally:
        await pool.close()
        main._PG_POOL = None

    assert response == {"deleted": True, "id": scope["decision_id"]}
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        remaining = await check.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE id = $1",
            scope["decision_id"],
        )
    finally:
        await check.close()
    assert remaining == 0


@pytest.mark.asyncio
async def test_decision_reference_trigger_rejects_cross_scope_and_closes_race(
    postgres_with_real_init_schema: str,
) -> None:
    scope_a = await _seed(postgres_with_real_init_schema)
    scope_b = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        with pytest.raises(asyncpg.CheckViolationError) as assignee_error:
            await admin.execute(
                """INSERT INTO decisions (
                       title, description, created_by_id, assignee_id,
                       visibility, workspace_id
                   ) VALUES ('cross-assignee-canary', '', $1, $2, 'private', $3)""",
                scope_a["user_id"],
                scope_b["user_id"],
                scope_a["workspace_id"],
            )
        assert assignee_error.value.constraint_name == "decisions_assignee_scope_check"

        with pytest.raises(asyncpg.CheckViolationError) as follow_error:
            await admin.execute(
                """UPDATE decisions SET follow_up_decision_id = $1
                    WHERE id = $2""",
                scope_b["decision_id"],
                scope_a["decision_id"],
            )
        assert follow_error.value.constraint_name == "decisions_follow_up_scope_check"

        await admin.execute("ALTER TABLE decisions DISABLE TRIGGER trg_decisions_reference_scope")
        legacy_id = await admin.fetchval(
            """INSERT INTO decisions (
                   title, description, created_by_id, assignee_id,
                   visibility, workspace_id
               ) VALUES ('legacy-cross-scope', '', $1, $2, 'private', $3)
               RETURNING id""",
            scope_a["user_id"],
            scope_b["user_id"],
            scope_a["workspace_id"],
        )
        await admin.execute("ALTER TABLE decisions ENABLE TRIGGER trg_decisions_reference_scope")
        await admin.execute(
            "UPDATE decisions SET title = 'legacy-unrelated-update' WHERE id = $1",
            legacy_id,
        )
    finally:
        await admin.close()

    writer = await asyncpg.connect(postgres_with_real_init_schema)
    concurrent = await asyncpg.connect(postgres_with_real_init_schema)
    writer_tx = writer.transaction()
    concurrent_tx = concurrent.transaction()
    try:
        await writer_tx.start()
        await writer.execute(
            """INSERT INTO decisions (
                   title, description, created_by_id, assignee_id,
                   visibility, workspace_id
               ) VALUES ('membership-lock-canary', '', $1, $1, 'private', $2)""",
            scope_a["user_id"],
            scope_a["workspace_id"],
        )
        await concurrent_tx.start()
        await concurrent.execute("SET LOCAL lock_timeout = '250ms'")
        with pytest.raises(asyncpg.LockNotAvailableError):
            await concurrent.execute(
                """DELETE FROM user_workspace_roles
                    WHERE user_id = $1 AND workspace_id = $2""",
                scope_a["user_id"],
                scope_a["workspace_id"],
            )
        await concurrent_tx.rollback()
        await writer_tx.commit()
    finally:
        if not concurrent.is_closed():
            await concurrent.close()
        if not writer.is_closed():
            await writer.close()
