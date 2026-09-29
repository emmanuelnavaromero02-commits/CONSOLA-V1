from __future__ import annotations

import hashlib
import secrets
import string
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
    / "99zzzzzzb_personal_access_tokens.sql"
)
CREATE_SQL = "SELECT * FROM omega_auth_create_access_token($1, $2, $3, $4, $5, $6, $7)"
RESOLVE_SQL = "SELECT * FROM omega_auth_resolve_access_token($1, $2)"
REVOKE_SQL = "SELECT * FROM omega_auth_revoke_access_token($1, $2)"
LIST_SQL = "SELECT * FROM omega_auth_list_access_tokens($1)"


def _role_dsn(admin_dsn: str, role: str, password: str) -> str:
    return admin_dsn.replace(
        f"{POSTGRES_USER}:{POSTGRES_PASSWORD}", f"{role}:{password}"
    )


def _material() -> tuple[str, str, str]:
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(43))
    raw = f"omega_pat_{body}"
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw[:14]


def _days(count: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=count)


async def _seed(admin_dsn: str) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_a = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id", f"pat-a-{suffix}"
        )
        tenant_b = await conn.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id", f"pat-b-{suffix}"
        )
        workspace_a = await conn.fetchval(
            "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
            tenant_a,
            f"PAT A {suffix}",
        )
        workspace_b = await conn.fetchval(
            "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
            tenant_b,
            f"PAT B {suffix}",
        )
        users: dict[str, int] = {}
        for key, role, active, must_change in (
            ("member", "user", True, False),
            ("limit", "user", True, False),
            ("admin", "admin", True, False),
            ("inactive", "user", True, False),
            ("must_change", "user", True, False),
            ("outsider", "user", True, False),
        ):
            users[key] = await conn.fetchval(
                """INSERT INTO users (email, name, password_hash, role, tenant_id, is_active, must_change_password)
                   VALUES ($1, $2, 'opaque-test-digest', $3, $4, $5, $6) RETURNING id""",
                f"pat-{key}-{suffix}@example.invalid",
                f"PAT {key}",
                role,
                tenant_a,
                active,
                must_change,
            )
        role_id = await conn.fetchval(
            "SELECT id FROM roles WHERE name IN ('viewer', 'workspace_user') ORDER BY id LIMIT 1"
        )
        await conn.executemany(
            "INSERT INTO user_workspace_roles (user_id, workspace_id, role_id) VALUES ($1, $2, $3)",
            [
                (users["member"], workspace_a, role_id),
                (users["limit"], workspace_a, role_id),
                (users["admin"], workspace_b, role_id),
                (users["inactive"], workspace_a, role_id),
                (users["must_change"], workspace_a, role_id),
                (users["outsider"], workspace_b, role_id),
            ],
        )
        return {
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "workspace_a": workspace_a,
            "workspace_b": workspace_b,
            **users,
        }
    finally:
        await conn.close()


async def _create(console, user_id, workspace_id, *, scopes=("lectura",), days=30):
    raw, digest, prefix = _material()
    row = await console.fetchrow(
        CREATE_SQL, user_id, workspace_id, "Asistente", digest, prefix, list(scopes), _days(days)
    )
    return raw, digest, row


@pytest.mark.asyncio
async def test_service_roles_cannot_touch_the_token_table(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    workspace = await asyncpg.connect(
        _role_dsn(postgres_with_real_init_schema, "omega_workspace", WORKSPACE_PASSWORD)
    )
    try:
        privileges = await admin.fetchrow(
            """SELECT
                 has_table_privilege('omega_console', 'user_access_tokens', 'SELECT') AS console_read,
                 has_table_privilege('omega_console', 'user_access_tokens', 'INSERT') AS console_insert,
                 has_table_privilege('omega_workspace', 'user_access_tokens', 'SELECT') AS workspace_read,
                 has_function_privilege('omega_workspace',
                     'omega_auth_resolve_access_token(text,text)', 'EXECUTE') AS workspace_resolve,
                 has_function_privilege('omega_console',
                     'omega_auth_resolve_access_token(text,text)', 'EXECUTE') AS console_resolve"""
        )
        assert dict(privileges) == {
            "console_read": False,
            "console_insert": False,
            "workspace_read": False,
            "workspace_resolve": False,
            "console_resolve": True,
        }
        security = await admin.fetchrow(
            """SELECT c.relrowsecurity, c.relforcerowsecurity
                 FROM pg_class c WHERE c.oid = 'public.user_access_tokens'::regclass"""
        )
        assert tuple(security) == (True, True)
        owners = await admin.fetch(
            """SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef
                 FROM pg_proc p
                WHERE p.proname IN (
                    'omega_auth_create_access_token', 'omega_auth_resolve_access_token',
                    'omega_auth_list_access_tokens', 'omega_auth_revoke_access_token',
                    'omega_auth_revoke_user_tokens')"""
        )
        assert {row["proname"] for row in owners} == {
            "omega_auth_create_access_token",
            "omega_auth_resolve_access_token",
            "omega_auth_list_access_tokens",
            "omega_auth_revoke_access_token",
            "omega_auth_revoke_user_tokens",
        }
        assert all(row["owner"] == "omega_auth" and row["prosecdef"] for row in owners)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await console.fetchval("SELECT token_hash FROM user_access_tokens LIMIT 1")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await workspace.fetchval("SELECT count(*) FROM user_access_tokens")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await workspace.fetch(RESOLVE_SQL, "a" * 64, None)
    finally:
        await workspace.close()
        await console.close()
        await admin.close()


@pytest.mark.asyncio
async def test_create_binds_workspace_and_stores_only_the_hash(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        raw, digest, row = await _create(console, scope["member"], scope["workspace_a"])
        assert row["tenant_id"] == scope["tenant_a"]
        assert row["workspace_id"] == scope["workspace_a"]
        assert row["scopes"] == ["lectura"]
        stored = await admin.fetchrow(
            "SELECT * FROM user_access_tokens WHERE id = $1", row["token_id"]
        )
        assert stored["token_hash"] == digest
        assert raw not in {str(value) for value in dict(stored).values()}

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _create(console, scope["member"], scope["workspace_b"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _create(console, scope["outsider"], scope["workspace_a"])
        await admin.execute(
            "UPDATE users SET must_change_password = TRUE WHERE id = $1", scope["must_change"]
        )
        await admin.execute("UPDATE users SET is_active = FALSE WHERE id = $1", scope["inactive"])
        for blocked in ("must_change", "inactive"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await _create(console, scope[blocked], scope["workspace_a"])
        _raw, _digest, admin_row = await _create(console, scope["admin"], scope["workspace_a"])
        assert admin_row["workspace_id"] == scope["workspace_a"]

        _raw, _digest, both = await _create(
            console, scope["member"], scope["workspace_a"], scopes=("acciones", "lectura", "acciones")
        )
        assert both["scopes"] == ["acciones", "lectura"]

        _raw_bad, digest_bad, prefix_bad = _material()
        for args in (
            (scope["member"], scope["workspace_a"], "x", "Z" * 64, prefix_bad, ["lectura"], _days(30)),
            (scope["member"], scope["workspace_a"], "x", digest_bad, "omega_pat_!!", ["lectura"], _days(30)),
            (scope["member"], scope["workspace_a"], "x", digest_bad, prefix_bad, ["admin"], _days(30)),
            (scope["member"], scope["workspace_a"], "x", digest_bad, prefix_bad, [], _days(30)),
            (scope["member"], scope["workspace_a"], "", digest_bad, prefix_bad, ["lectura"], _days(30)),
            (scope["member"], scope["workspace_a"], "x" * 81, digest_bad, prefix_bad, ["lectura"], _days(30)),
            (scope["member"], scope["workspace_a"], "x", digest_bad, prefix_bad, ["lectura"], _days(91)),
            (scope["member"], scope["workspace_a"], "x", digest_bad, prefix_bad, ["lectura"], _days(-1)),
        ):
            with pytest.raises(asyncpg.InvalidParameterValueError):
                await console.fetchrow(CREATE_SQL, *args)
        with pytest.raises(asyncpg.CheckViolationError):
            await admin.execute(
                """INSERT INTO user_access_tokens
                     (user_id, tenant_id, workspace_id, name, token_hash, token_prefix, scopes, expires_at)
                   VALUES ($1, $2, $3, 'x', $4, 'omega_pat_abcd', ARRAY['lectura'], clock_timestamp() + interval '91 days')""",
                scope["member"],
                scope["tenant_a"],
                scope["workspace_a"],
                "b" * 64,
            )
    finally:
        await console.close()
        await admin.close()


@pytest.mark.asyncio
async def test_eleventh_active_token_is_rejected(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        created = [await _create(console, scope["limit"], scope["workspace_a"]) for _ in range(10)]
        with pytest.raises(asyncpg.ConfigurationLimitExceededError):
            await _create(console, scope["limit"], scope["workspace_a"])
        first_id = created[0][2]["token_id"]
        revoked = await console.fetchrow(REVOKE_SQL, scope["limit"], first_id)
        assert revoked["token_id"] == first_id
        await _create(console, scope["limit"], scope["workspace_a"])
    finally:
        await console.close()


@pytest.mark.asyncio
async def test_resolve_statuses_throttle_and_revocation(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        _raw, digest, row = await _create(console, scope["member"], scope["workspace_a"])
        resolved = await console.fetchrow(RESOLVE_SQL, digest, "203.0.113.7")
        assert resolved["status"] == "activo"
        assert resolved["user_id"] == scope["member"]
        assert resolved["workspace_id"] == scope["workspace_a"]
        assert resolved["tenant_id"] == scope["tenant_a"]
        assert resolved["scopes"] == ["lectura"]
        assert "token_hash" not in dict(resolved)
        first_use = await admin.fetchval(
            "SELECT last_used_at FROM user_access_tokens WHERE id = $1", row["token_id"]
        )
        assert first_use is not None
        await console.fetchrow(RESOLVE_SQL, digest, "203.0.113.8")
        assert tuple(
            await admin.fetchrow(
                "SELECT last_used_at, last_used_ip FROM user_access_tokens WHERE id = $1",
                row["token_id"],
            )
        ) == (first_use, "203.0.113.7")
        await admin.execute(
            "UPDATE user_access_tokens SET last_used_at = last_used_at - interval '6 minutes' WHERE id = $1",
            row["token_id"],
        )
        await console.fetchrow(RESOLVE_SQL, digest, "203.0.113.9")
        assert await admin.fetchval(
            "SELECT last_used_ip FROM user_access_tokens WHERE id = $1", row["token_id"]
        ) == "203.0.113.9"

        assert await console.fetch(RESOLVE_SQL, "c" * 64, None) == []
        assert await console.fetch(RESOLVE_SQL, "not-a-hash", None) == []

        _raw, expired_digest, expired = await _create(console, scope["member"], scope["workspace_a"])
        await admin.execute(
            """UPDATE user_access_tokens
                  SET created_at = clock_timestamp() - interval '40 days',
                      expires_at = clock_timestamp() - interval '10 days'
                WHERE id = $1""",
            expired["token_id"],
        )
        assert (await console.fetchrow(RESOLVE_SQL, expired_digest, None))["status"] == "vencido"

        _raw, inactive_digest, _row = await _create(console, scope["inactive"], scope["workspace_a"])
        await admin.execute("UPDATE users SET is_active = FALSE WHERE id = $1", scope["inactive"])
        assert (await console.fetchrow(RESOLVE_SQL, inactive_digest, None))["status"] == "usuario_inactivo"

        _raw, must_digest, _row = await _create(console, scope["must_change"], scope["workspace_a"])
        await admin.execute(
            "UPDATE users SET must_change_password = TRUE WHERE id = $1", scope["must_change"]
        )
        assert (await console.fetchrow(RESOLVE_SQL, must_digest, None))["status"] == "cambio_de_contrasena"

        assert await console.fetchrow(REVOKE_SQL, scope["outsider"], row["token_id"]) is None
        revoked = await console.fetchrow(REVOKE_SQL, scope["member"], row["token_id"])
        assert dict(revoked) == {
            "token_id": row["token_id"],
            "workspace_id": scope["workspace_a"],
            "token_prefix": row["token_prefix"],
            "scopes": ["lectura"],
            "expires_at": row["expires_at"],
        }
        assert await console.fetchrow(REVOKE_SQL, scope["member"], row["token_id"]) is None
        assert (await console.fetchrow(RESOLVE_SQL, digest, None))["status"] == "revocado"

        listed = await console.fetch(LIST_SQL, scope["member"])
        assert listed
        assert all("token_hash" not in dict(item) for item in listed)
        assert {item["status"] for item in listed} >= {"revocado", "vencido"}
    finally:
        await console.close()
        await admin.close()


@pytest.mark.asyncio
async def test_listing_puts_live_tokens_first_and_revocation_ignores_the_page(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        _raw, _digest, live = await _create(console, scope["member"], scope["workspace_a"])
        _raw, _digest, stale = await _create(console, scope["member"], scope["workspace_a"])
        await admin.execute(
            """UPDATE user_access_tokens
                  SET created_at = clock_timestamp() - interval '30 days',
                      expires_at = clock_timestamp() + interval '30 days'
                WHERE id = $1""",
            live["token_id"],
        )
        await admin.execute(
            """UPDATE user_access_tokens
                  SET created_at = clock_timestamp() - interval '60 days',
                      expires_at = clock_timestamp() - interval '1 day'
                WHERE id = $1""",
            stale["token_id"],
        )
        await admin.execute(
            """INSERT INTO user_access_tokens (
                   user_id, tenant_id, workspace_id, name, token_hash, token_prefix,
                   scopes, created_at, expires_at, revoked_at, revoked_reason
               )
               SELECT $1, $2, $3, 'Anterior ' || g,
                      encode(sha256(convert_to($4 || g::text, 'UTF8')), 'hex'),
                      'omega_pat_Abcd', ARRAY['lectura'],
                      clock_timestamp() - make_interval(mins => g),
                      clock_timestamp() + interval '1 day',
                      clock_timestamp(), 'usuario'
                 FROM generate_series(1, 105) AS g""",
            scope["member"],
            scope["tenant_a"],
            scope["workspace_a"],
            uuid.uuid4().hex,
        )
        listed = await console.fetch(LIST_SQL, scope["member"])
        assert len(listed) == 100
        assert listed[0]["token_id"] == live["token_id"]
        assert listed[0]["status"] == "activo"
        assert stale["token_id"] not in {item["token_id"] for item in listed}
        revoked = await console.fetchrow(REVOKE_SQL, scope["member"], stale["token_id"])
        assert revoked["token_id"] == stale["token_id"]
        assert await console.fetchrow(REVOKE_SQL, scope["outsider"], live["token_id"]) is None
    finally:
        await console.close()
        await admin.close()


@pytest.mark.asyncio
async def test_listing_marks_tokens_whose_owner_lost_access(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        _raw, member_digest, member_row = await _create(console, scope["member"], scope["workspace_a"])
        _raw, _digest, admin_row = await _create(console, scope["admin"], scope["workspace_a"])
        _raw, _digest, inactive_row = await _create(console, scope["inactive"], scope["workspace_a"])

        def status_of(rows, token_id):
            return next(item["status"] for item in rows if item["token_id"] == token_id)

        assert status_of(await console.fetch(LIST_SQL, scope["member"]), member_row["token_id"]) == "activo"
        await admin.execute(
            "DELETE FROM user_workspace_roles WHERE user_id = $1 AND workspace_id = $2",
            scope["member"],
            scope["workspace_a"],
        )
        assert status_of(await console.fetch(LIST_SQL, scope["member"]), member_row["token_id"]) == "sin_acceso"
        assert (await console.fetchrow(RESOLVE_SQL, member_digest, None))["status"] == "activo"
        assert status_of(await console.fetch(LIST_SQL, scope["admin"]), admin_row["token_id"]) == "activo"
        await admin.execute("UPDATE users SET is_active = FALSE WHERE id = $1", scope["inactive"])
        assert status_of(await console.fetch(LIST_SQL, scope["inactive"]), inactive_row["token_id"]) == "sin_acceso"
    finally:
        await console.close()
        await admin.close()


@pytest.mark.asyncio
async def test_credential_reset_revokes_every_access_token(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        _raw, digest_one, _row = await _create(console, scope["member"], scope["workspace_a"])
        _raw, digest_two, _row = await _create(console, scope["member"], scope["workspace_a"])
        session_digest = hashlib.sha256(secrets.token_hex(32).encode("utf-8")).hexdigest()
        await console.fetchval(
            "SELECT omega_auth_create_session($1, $2, $3, $4)",
            session_digest,
            scope["member"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            None,
        )
        revoked = await console.fetchval("SELECT omega_auth_revoke_user_tokens($1)", scope["member"])
        assert revoked >= 3
        reasons = await admin.fetch(
            "SELECT revoked_reason FROM user_access_tokens WHERE user_id = $1", scope["member"]
        )
        assert {row["revoked_reason"] for row in reasons} == {"credenciales_restablecidas"}
        for digest in (digest_one, digest_two):
            assert (await console.fetchrow(RESOLVE_SQL, digest, None))["status"] == "revocado"
        assert await admin.fetchval(
            "SELECT count(*) FROM user_sessions WHERE token_hash = $1", session_digest
        ) == 0
    finally:
        await console.close()
        await admin.close()


@pytest.mark.asyncio
async def test_migration_replays_without_touching_existing_tokens(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    scope = await _seed(postgres_with_real_init_schema)
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    console = await asyncpg.connect(omega_console_live_dsn)
    try:
        _raw, digest, _row = await _create(console, scope["member"], scope["workspace_a"])
        migration_sql = MIGRATION.read_text(encoding="utf-8")
        for _replay in range(2):
            await admin.execute(migration_sql)
            assert (await console.fetchrow(RESOLVE_SQL, digest, None))["status"] == "activo"
        assert await admin.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename = $1", MIGRATION.name
        ) == 1
    finally:
        await console.close()
        await admin.close()
