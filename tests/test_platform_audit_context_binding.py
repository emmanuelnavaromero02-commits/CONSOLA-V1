from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import pytest
import sqlglot

REPO = Path(__file__).resolve().parents[1]
INIT = REPO / "infra" / "init"
MIGRATION = INIT / "99zzzzr_platform_audit_requires_platform_admin.sql"
DEFINITION = re.compile(
    r"CREATE OR REPLACE FUNCTION omega_20b_platform_audit_context\(\)\s*"
    r"RETURNS boolean\s*LANGUAGE sql\s*STABLE\s*AS \$\$(?P<body>.*?)\$\$;",
    re.DOTALL,
)


def _definitions() -> list[tuple[str, str]]:
    found = []
    for path in sorted(INIT.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "FUNCTION omega_20b_platform_audit_context" in text:
            match = DEFINITION.search(text)
            assert match, f"{path.name} redefines the function with other attributes"
            found.append((path.name, match.group("body")))
    return found


def test_the_new_definition_is_the_last_one_and_keeps_the_attributes():
    definitions = _definitions()
    assert [name for name, _ in definitions] == [
        "99w_tenant_isolation_20b.sql",
        MIGRATION.name,
    ]
    names = sorted(path.name for path in INIT.glob("*.sql"))
    for user in ("99w_tenant_isolation_20b.sql", "99x_cartridge_kb_scope_20c.sql",
                 "99zzw_sap_cartridge_runlog_scope_repair.sql", "99zzzzl_sap_b1_cartridge_role.sql"):
        assert names.index(user) < names.index(MIGRATION.name)
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" not in sql
    assert "VALUES ('99zzzzr_platform_audit_requires_platform_admin.sql', NOW())" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_platform_audit_requires_no_workspace_and_the_explicit_flag():
    _, body = _definitions()[-1]
    predicate = sqlglot.parse_one(body.strip(), read="postgres").expressions[0].sql(dialect="postgres")
    assert predicate == (
        "NULLIF(CURRENT_SETTING('app.workspace_id', TRUE), '') IS NULL "
        "AND COALESCE(CURRENT_SETTING('app.platform_admin', TRUE), '') = 'true'"
    )
    _, legacy = _definitions()[0]
    assert "platform_admin" not in legacy


# Live PostgreSQL with the real init schema and the real console role.

import asyncpg  # noqa: E402

from tests.test_operational_rls_console_refinement import (  # noqa: E402
    OMEGA_CONSOLE_PASSWORD,
    postgres_with_real_init_schema,
)


async def _as_console(dsn: str, gucs: dict[str, str]) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn.replace("postgres:test_postgres_password@", f"omega_console:{OMEGA_CONSOLE_PASSWORD}@"))
    for name, value in gucs.items():
        await conn.execute("SELECT set_config($1, $2, false)", name, value)
    return conn


async def _visible(dsn: str, gucs: dict[str, str], run_ids: list[str]) -> set[str]:
    conn = await _as_console(dsn, gucs)
    try:
        rows = await conn.fetch("SELECT run_id FROM extraction_runs WHERE run_id = ANY($1::text[])", run_ids)
        return {row["run_id"] for row in rows}
    finally:
        await conn.close()


async def _insert_template(dsn: str, gucs: dict[str, str], name: str) -> None:
    conn = await _as_console(dsn, gucs)
    try:
        await conn.execute(
            """INSERT INTO analytic_apps
                   (name, title, html, cartridge_id, created_by_id, visibility,
                    tenant_id, workspace_id, scope_status)
               VALUES ($1, 'Plantilla', '<html></html>', NULL, NULL, 'shared',
                       NULL, NULL, 'platform_template')""",
            name,
        )
    finally:
        await conn.close()


async def _scenario(dsn: str) -> dict:
    admin = await asyncpg.connect(dsn)
    suffix = uuid.uuid4().hex[:10]
    legacy, scoped = f"legacy-{suffix}", f"scoped-{suffix}"
    template_denied, template_allowed = f"tpl_denied_{suffix}", f"tpl_allowed_{suffix}"
    try:
        tenant = await admin.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id::text", f"audit-{suffix}"
        )
        workspace = await admin.fetchval(
            "INSERT INTO workspaces (tenant_id, name) VALUES ($1::uuid, $2) RETURNING id::text",
            tenant,
            f"audit-{suffix}",
        )
        await admin.execute(
            """INSERT INTO extraction_runs (run_id, cartridge_id, status, scope_status, tenant_id, workspace_id)
               VALUES ($1, 'sap_b1', 'failed', 'legacy_unscoped', NULL, NULL),
                      ($2, 'sap_b1', 'completed', 'scoped', $3::uuid, $4::uuid)""",
            legacy,
            scoped,
            tenant,
            workspace,
        )
        ids = [legacy, scoped]
        scope = {"app.tenant_id": tenant, "app.workspace_id": workspace}
        result = {
            "unscoped": await _visible(dsn, {}, ids),
            "unscoped_flag_false": await _visible(dsn, {"app.platform_admin": "false"}, ids),
            "platform_admin": await _visible(dsn, {"app.platform_admin": "true"}, ids),
            "scoped": await _visible(dsn, scope, ids),
            "scoped_with_flag": await _visible(dsn, {**scope, "app.platform_admin": "true"}, ids),
        }
        try:
            await _insert_template(dsn, {}, template_denied)
            result["template_without_flag"] = "inserted"
        except asyncpg.InsufficientPrivilegeError:
            result["template_without_flag"] = "denied"
        await _insert_template(dsn, {"app.platform_admin": "true"}, template_allowed)
        result["template_rows"] = await admin.fetchval(
            "SELECT count(*) FROM analytic_apps WHERE name = ANY($1::text[])",
            [template_denied, template_allowed],
        )
        reader = await _as_console(dsn, {})
        try:
            result["template_read_without_flag"] = await reader.fetchval(
                "SELECT count(*) FROM analytic_apps WHERE name = $1", template_allowed
            )
        finally:
            await reader.close()
        result["ids"] = (legacy, scoped)
        return result
    finally:
        await admin.execute("DELETE FROM analytic_apps WHERE name = ANY($1::text[])", [template_denied, template_allowed])
        await admin.execute("DELETE FROM extraction_runs WHERE run_id = ANY($1::text[])", [legacy, scoped])
        await admin.close()


def test_live_legacy_rows_and_templates_require_the_platform_flag(postgres_with_real_init_schema):
    result = asyncio.run(_scenario(postgres_with_real_init_schema))
    legacy, scoped = result["ids"]
    assert result["unscoped"] == set()
    assert result["unscoped_flag_false"] == set()
    assert result["platform_admin"] == {legacy}
    assert result["scoped"] == {scoped}
    assert result["scoped_with_flag"] == {scoped}
    assert result["template_without_flag"] == "denied"
    assert result["template_rows"] == 1
    assert result["template_read_without_flag"] == 1
