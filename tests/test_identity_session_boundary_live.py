"""DB-gated canaries for the F-SEG identity/session boundary."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


WORKSPACE_PASSWORD = "test_omega_workspace_password"
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "init"
    / "99zzy_identity_session_boundary.sql"
)


def _role_dsn(admin_dsn: str, role: str, password: str) -> str:
    return admin_dsn.replace(
        f"{POSTGRES_USER}:{POSTGRES_PASSWORD}", f"{role}:{password}"
    )


async def _seed_identity(admin_dsn: str) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_a = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            f"identity-a-{suffix}",
            f"identity-a-{suffix}",
        )
        tenant_b = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
            f"identity-b-{suffix}",
            f"identity-b-{suffix}",
        )
        workspace_a = await conn.fetchval(
            "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
            tenant_a,
            f"Identity A {suffix}",
        )
        workspace_b = await conn.fetchval(
            "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
            tenant_b,
            f"Identity B {suffix}",
        )
        user_a = await conn.fetchval(
            """INSERT INTO users (email, name, password_hash, role, tenant_id, is_active)
               VALUES ($1, 'Scoped user A', $2, 'user', $3, TRUE) RETURNING id""",
            f"identity-a-{suffix}@example.invalid",
            "opaque-test-password-digest-a",
            tenant_a,
        )
        admin_b = await conn.fetchval(
            """INSERT INTO users (email, name, password_hash, role, tenant_id, is_active)
               VALUES ($1, 'Admin canary B', $2, 'admin', $3, TRUE) RETURNING id""",
            f"identity-b-{suffix}@example.invalid",
            "opaque-test-password-digest-b",
            tenant_b,
        )
        role_id = await conn.fetchval(
            "SELECT id FROM roles WHERE name IN ('viewer', 'workspace_user') ORDER BY id LIMIT 1"
        )
        assert role_id is not None
        await conn.executemany(
            "INSERT INTO user_workspace_roles (user_id, workspace_id, role_id) VALUES ($1, $2, $3)",
            [(user_a, workspace_a, role_id), (admin_b, workspace_b, role_id)],
        )
        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "workspace_a": workspace_a,
            "workspace_b": workspace_b,
            "user_a": user_a,
            "admin_b": admin_b,
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_workspace_cannot_read_secrets_or_forge_admin_session(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed_identity(postgres_with_real_init_schema)
    workspace_dsn = _role_dsn(
        postgres_with_real_init_schema, "omega_workspace", WORKSPACE_PASSWORD
    )
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    workspace = await asyncpg.connect(workspace_dsn)
    console = await asyncpg.connect(omega_console_live_dsn)
    raw_session = secrets.token_hex(32)
    digest = hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
    try:
        privileges = await admin.fetchrow(
            """SELECT
                 has_column_privilege('omega_workspace', 'users', 'password_hash', 'SELECT') AS password_read,
                 has_table_privilege('omega_workspace', 'user_sessions', 'SELECT') AS session_read,
                 has_table_privilege('omega_workspace', 'user_sessions', 'INSERT') AS session_insert,
                 has_table_privilege('omega_workspace', 'user_sessions', 'UPDATE') AS session_update"""
        )
        assert dict(privileges) == {
            "password_read": False,
            "session_read": False,
            "session_insert": False,
            "session_update": False,
        }
        auth_role = await admin.fetchrow(
            """SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                      rolreplication, rolinherit, rolbypassrls
                 FROM pg_roles WHERE rolname = 'omega_auth'"""
        )
        assert dict(auth_role) == {
            "rolcanlogin": False,
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolinherit": False,
            "rolbypassrls": False,
        }
        assert await admin.fetchval(
            """SELECT to_regprocedure(
                    'public.omega_auth_resolve_workspace_session(text,uuid,timestamptz,timestamptz,timestamptz)'
                ) IS NULL"""
        ) is True

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await workspace.fetchval("SELECT password_hash FROM users LIMIT 1")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await workspace.fetchval("SELECT token_hash FROM user_sessions LIMIT 1")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await workspace.execute(
                "INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, NOW() + interval '1 hour')",
                "a" * 64,
                scope["admin_b"],
            )

        async with workspace.transaction():
            await workspace.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                str(scope["tenant_a"]),
                str(scope["workspace_a"]),
            )
            visible_users = await workspace.fetch(
                "SELECT id FROM users ORDER BY id"
            )
            visible_memberships = await workspace.fetch(
                "SELECT user_id FROM user_workspace_roles ORDER BY user_id"
            )
        assert [row["id"] for row in visible_users] == [scope["user_a"]]
        assert [row["user_id"] for row in visible_memberships] == [scope["user_a"]]

        await console.fetchval(
            "SELECT omega_auth_create_session($1, $2, $3, $4)",
            digest,
            scope["user_a"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            None,
        )
        columns = {
            row["column_name"]
            for row in await admin.fetch(
                """SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'user_sessions'"""
            )
        }
        assert "token" not in columns
        assert "token_hash" in columns
        stored = await admin.fetchval(
            "SELECT token_hash FROM user_sessions WHERE user_id = $1", scope["user_a"]
        )
        assert stored == digest
        assert stored != raw_session

        resolved = await workspace.fetch(
            """SELECT user_id, workspace_id, tenant_id
                 FROM omega_auth_resolve_workspace_session($1, $2)""",
            digest,
            scope["workspace_a"],
        )
        assert [(r["user_id"], r["workspace_id"], r["tenant_id"]) for r in resolved] == [
            (scope["user_a"], scope["workspace_a"], scope["tenant_a"])
        ]
        denied_workspace = await workspace.fetch(
            """SELECT user_id
                 FROM omega_auth_resolve_workspace_session($1, $2)""",
            digest,
            scope["workspace_b"],
        )
        assert denied_workspace == []

        # A compromised Workspace role can no longer choose its own clock,
        # sliding threshold, or absolute-lifetime cutoff.
        with pytest.raises(asyncpg.UndefinedFunctionError):
            await workspace.fetch(
                """SELECT * FROM omega_auth_resolve_workspace_session(
                       $1, $2, $3, $4, $5
                   )""",
                digest,
                scope["workspace_a"],
                datetime.now(timezone.utc) + timedelta(days=3650),
                datetime.now(timezone.utc) + timedelta(days=3650),
                datetime.now(timezone.utc) - timedelta(days=3650),
            )

        stale_raw = secrets.token_hex(32)
        stale_digest = hashlib.sha256(stale_raw.encode("utf-8")).hexdigest()
        await console.fetchval(
            "SELECT omega_auth_create_session($1, $2, $3, $4)",
            stale_digest,
            scope["user_a"],
            datetime.now(timezone.utc) + timedelta(days=30),
            None,
        )
        await admin.execute(
            """UPDATE user_sessions
                  SET created_at = NOW() - interval '13 hours',
                      expires_at = NOW() + interval '30 days'
                WHERE token_hash = $1""",
            stale_digest,
        )
        assert await workspace.fetch(
            "SELECT * FROM omega_auth_resolve_workspace_session($1, $2)",
            stale_digest,
            scope["workspace_a"],
        ) == []
        assert await admin.fetchval(
            "SELECT count(*) FROM user_sessions WHERE token_hash = $1", stale_digest
        ) == 0

        logout_session_raw = secrets.token_hex(32)
        logout_refresh_raw = secrets.token_urlsafe(32)
        logout_session_digest = hashlib.sha256(
            logout_session_raw.encode("utf-8")
        ).hexdigest()
        logout_refresh_digest = hashlib.sha256(
            logout_refresh_raw.encode("utf-8")
        ).hexdigest()
        await console.fetchval(
            "SELECT omega_auth_create_session($1, $2, $3, $4)",
            logout_session_digest,
            scope["user_a"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            None,
        )
        await console.fetchval(
            "SELECT omega_auth_create_refresh_token($1, $2, $3)",
            scope["user_a"],
            logout_refresh_digest,
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        logout_result = await workspace.fetchrow(
            "SELECT * FROM omega_auth_logout($1, $2)",
            logout_session_digest,
            logout_refresh_digest,
        )
        assert dict(logout_result) == {
            "session_deleted": True,
            "refresh_revoked": True,
        }
        assert await admin.fetchval(
            "SELECT count(*) FROM user_sessions WHERE token_hash = $1",
            logout_session_digest,
        ) == 0
        assert await admin.fetchval(
            """SELECT count(*) FROM refresh_tokens
                WHERE token_hash = $1 AND revoked_at IS NULL""",
            logout_refresh_digest,
        ) == 0
        second_logout = await workspace.fetchrow(
            "SELECT * FROM omega_auth_logout($1, $2)",
            logout_session_digest,
            logout_refresh_digest,
        )
        assert dict(second_logout) == {
            "session_deleted": False,
            "refresh_revoked": False,
        }

        assert await workspace.fetchval(
            "SELECT omega_auth_destroy_session($1)", digest
        ) is True
        assert await admin.fetchval(
            "SELECT count(*) FROM user_sessions WHERE token_hash = $1", digest
        ) == 0
    finally:
        await console.close()
        await workspace.close()
        await admin.close()


@pytest.mark.asyncio
async def test_identity_boundary_migration_survives_two_replays(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed_identity(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    raw_session = secrets.token_hex(32)
    digest = hashlib.sha256(raw_session.encode("utf-8")).hexdigest()
    try:
        await console.fetchval(
            "SELECT omega_auth_create_session($1, $2, $3, $4)",
            digest,
            scope["user_a"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            None,
        )
        migration_sql = MIGRATION.read_text(encoding="utf-8")
        for _replay in range(2):
            await admin.execute(migration_sql)
            assert await admin.fetchval(
                "SELECT count(*) FROM user_sessions WHERE token_hash = $1", digest
            ) == 1
            assert await admin.fetchval(
                """SELECT NOT has_table_privilege(
                           'omega_workspace', 'user_sessions', 'INSERT'
                       )"""
            ) is True
            assert await admin.fetchval(
                """SELECT has_function_privilege(
                           'omega_workspace',
                           'omega_auth_logout(text,text)',
                           'EXECUTE'
                       )"""
            ) is True
    finally:
        await console.close()
        await admin.close()
