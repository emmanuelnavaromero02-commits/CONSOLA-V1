"""audit_events append-only guarantees, against the real infra/init schema.

Structural checks for 99zzzzk on a live database built from infra/init:
the conversation foreign key is gone, the trigger function is owned by
postgres with a pinned search_path, the application role cannot mutate audit
rows, and the migration is idempotent. Source-level checks live in
tests/test_audit_events_append_only_contract.py.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _seed_conversation_with_audit(admin_dsn: str) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_id = str(
            await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id",
                f"audit-append-only-{suffix}",
            )
        )
        workspace_id = str(
            await conn.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1::uuid, $2) RETURNING id",
                tenant_id,
                f"audit append-only {suffix}",
            )
        )
        user_id = await conn.fetchval(
            "INSERT INTO users (email, name, password_hash, role, tenant_id) "
            "VALUES ($1, 'audit probe', 'x', 'user', $2::uuid) RETURNING id",
            f"audit-{suffix}@example.com",
            tenant_id,
        )
        conversation_id = str(
            await conn.fetchval(
                "INSERT INTO conversations (user_id, workspace_id, title) "
                "VALUES ($1, $2::uuid, 'audit probe') RETURNING id",
                user_id,
                workspace_id,
            )
        )
        audit_id = await conn.fetchval(
            "INSERT INTO audit_events (action, email, conversation_id) "
            "VALUES ('audit.append_only.probe', $1, $2::uuid) RETURNING id",
            f"audit-{suffix}@example.com",
            conversation_id,
        )
        return {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "audit_id": audit_id,
        }
    finally:
        await conn.close()


async def _cleanup(admin_dsn: str, seed: dict[str, object]) -> None:
    # Runs as the owner, which is exactly the maintenance path that must keep
    # working after the hardening.
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("DELETE FROM audit_events WHERE id = $1", seed["audit_id"])
        await conn.execute(
            "DELETE FROM conversations WHERE id = $1::uuid", seed["conversation_id"]
        )
        await conn.execute("DELETE FROM users WHERE id = $1", seed["user_id"])
        await conn.execute(
            "DELETE FROM workspaces WHERE id = $1::uuid", seed["workspace_id"]
        )
        await conn.execute("DELETE FROM tenants WHERE id = $1::uuid", seed["tenant_id"])
    finally:
        await conn.close()


@pytest.fixture()
def seeded(postgres_with_real_init_schema: str):
    seed = asyncio.run(_seed_conversation_with_audit(postgres_with_real_init_schema))
    yield seed
    asyncio.run(_cleanup(postgres_with_real_init_schema, seed))


def test_audit_events_no_longer_references_conversations(
    postgres_with_real_init_schema: str,
) -> None:
    async def run() -> int:
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            return await conn.fetchval(
                """
                SELECT count(*)
                  FROM pg_catalog.pg_constraint c
                  JOIN pg_catalog.pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                 WHERE c.conrelid = 'public.audit_events'::regclass
                   AND c.contype = 'f'
                   AND a.attname = 'conversation_id'
                """
            )
        finally:
            await conn.close()

    assert asyncio.run(run()) == 0


def test_trigger_function_is_owned_by_postgres_and_pins_search_path(
    postgres_with_real_init_schema: str,
) -> None:
    async def run() -> asyncpg.Record:
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            return await conn.fetchrow(
                "SELECT pg_get_userbyid(proowner) AS owner, proconfig "
                "FROM pg_proc WHERE oid = 'public.audit_events_append_only()'::regprocedure"
            )
        finally:
            await conn.close()

    row = asyncio.run(run())
    assert row["owner"] == "postgres"
    assert "search_path=pg_catalog, pg_temp" in (row["proconfig"] or [])


def test_app_role_cannot_mutate_audit_rows_directly(
    omega_console_live_dsn: str, seeded: dict[str, object]
) -> None:
    async def run() -> list[str]:
        conn = await asyncpg.connect(omega_console_live_dsn)
        failures = []
        try:
            for statement in (
                "DELETE FROM audit_events WHERE id = $1",
                "UPDATE audit_events SET action = 'forged' WHERE id = $1",
            ):
                try:
                    async with conn.transaction():
                        await conn.execute(statement, seeded["audit_id"])
                except asyncpg.InsufficientPrivilegeError as exc:
                    failures.append(str(exc))
            try:
                async with conn.transaction():
                    await conn.execute("TRUNCATE audit_events")
            except asyncpg.InsufficientPrivilegeError as exc:
                failures.append(str(exc))
        finally:
            await conn.close()
        return failures

    failures = asyncio.run(run())
    assert len(failures) == 3
    assert all("permission denied" in message for message in failures)


def test_hardening_migration_is_idempotent(postgres_with_real_init_schema: str) -> None:
    # A fresh init applies it once; operators re-run files by hand. A second
    # pass must succeed and leave every guarantee in place — the closing check
    # inside the file raises if any of them is missing.
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "infra/init/99zzzzk_audit_events_append_only_hardening.sql"
    ).read_text(encoding="utf-8")

    async def run() -> tuple[int, str]:
        conn = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            async with conn.transaction():
                await conn.execute(sql)
            fks = await conn.fetchval(
                """
                SELECT count(*) FROM pg_catalog.pg_constraint c
                  JOIN pg_catalog.pg_attribute a
                    ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                 WHERE c.conrelid = 'public.audit_events'::regclass
                   AND c.contype = 'f' AND a.attname = 'conversation_id'
                """
            )
            owner = await conn.fetchval(
                "SELECT pg_get_userbyid(proowner) FROM pg_proc "
                "WHERE oid = 'public.audit_events_append_only()'::regprocedure"
            )
            return fks, owner
        finally:
            await conn.close()

    assert asyncio.run(run()) == (0, "postgres")
