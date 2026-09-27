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


def _role_dsn(dsn: str, role: str, password: str) -> str:
    return dsn.replace("postgres:test_postgres_password@", f"{role}:{password}@")


def _load_service_module(monkeypatch, service: str, module: str, env: dict[str, str] | None = None):
    import importlib
    import sys

    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    services = tuple(str(REPO / name) for name in ("console", "mcp-infra", "refinement"))
    monkeypatch.setattr(sys, "path", [str(REPO / service), *(p for p in sys.path if p not in services)])
    return importlib.import_module(module)


def test_live_console_startup_seeds_write_platform_templates_under_the_flag(
    postgres_with_real_init_schema, monkeypatch
):
    from types import SimpleNamespace

    from app.domains.system.lifespan import run_packaged_startup_seeds
    from app.services import seed_dag_sources, seed_packaged_apps, seed_packaged_datasets, seed_packaged_hints

    for module in (seed_packaged_apps, seed_packaged_datasets, seed_packaged_hints):
        monkeypatch.setattr(module, "_REGISTRY", REPO / "cartridges")
    monkeypatch.setattr(seed_dag_sources, "_DAG_SEARCH_PATHS", [REPO / "cartridges"])

    async def scenario() -> tuple[list, int, int]:
        pool = await asyncpg.create_pool(
            _role_dsn(postgres_with_real_init_schema, "omega_console", OMEGA_CONSOLE_PASSWORD),
            min_size=1,
            max_size=2,
        )
        failures: list[tuple[str, str]] = []

        async def get_db_pool():
            return pool

        async def run_startup_seed(_app, component, seed):
            try:
                await seed()
            except Exception as exc:  # noqa: BLE001
                failures.append((component, repr(exc)))

        try:
            for _ in range(2):
                await run_packaged_startup_seeds(
                    SimpleNamespace(), get_db_pool=get_db_pool, run_startup_seed=run_startup_seed
                )
            async with pool.acquire() as conn:
                unflagged = await conn.fetchval(
                    "SELECT count(*) FROM analytic_apps WHERE scope_status = 'platform_template'"
                )
        finally:
            await pool.close()
        admin = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            seeded = await admin.fetchval(
                "SELECT count(*) FROM analytic_apps WHERE scope_status = 'platform_template'"
            )
        finally:
            await admin.close()
        return failures, seeded, unflagged

    failures, seeded, unflagged = asyncio.run(scenario())
    packaged = len(list(REPO.glob("cartridges/*/apps/*.html")))
    assert failures == []
    assert seeded == packaged > 0
    assert unflagged == seeded


def _seed_legacy_rows(dsn: str, suffix: str) -> tuple[str, str, str, str]:
    async def seed() -> tuple[str, str, str, str]:
        admin = await asyncpg.connect(dsn)
        try:
            tenant = await admin.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id::text", f"combo-{suffix}"
            )
            workspace = await admin.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1::uuid, $2) RETURNING id::text",
                tenant,
                f"combo-{suffix}",
            )
            await admin.execute(
                """INSERT INTO extraction_runs (run_id, cartridge_id, status, scope_status, tenant_id, workspace_id)
                   VALUES ($1, 'sap_b1', 'failed', 'legacy_unscoped', NULL, NULL),
                          ($2, 'sap_b1', 'completed', 'scoped', $3::uuid, $4::uuid)""",
                f"legacy-{suffix}",
                f"scoped-{suffix}",
                tenant,
                workspace,
            )
            await admin.execute(
                """INSERT INTO data_catalog (dataset, layer, cartridge, column_name, tenant_id, workspace_id, scope_status)
                   VALUES ($1, 'silver', 'sap_b1', 'legacy_col', NULL, NULL, 'legacy_unscoped'),
                          ($1, 'silver', 'sap_b1', 'scoped_col', $2::uuid, $3::uuid, 'scoped')""",
                f"combo_{suffix}",
                tenant,
                workspace,
            )
        finally:
            await admin.close()
        return tenant, workspace, f"legacy-{suffix}", f"scoped-{suffix}"

    return asyncio.run(seed())


def test_live_mcp_infra_and_refinement_unscoped_admin_paths_still_read_legacy_rows(
    postgres_with_real_init_schema, monkeypatch
):
    import psycopg2

    suffix = uuid.uuid4().hex[:10]
    tenant, workspace, legacy, scoped = _seed_legacy_rows(postgres_with_real_init_schema, suffix)
    pipeline = _load_service_module(
        monkeypatch,
        "mcp-infra",
        "app.tools.pipeline",
        {
            "AIRFLOW_URL": "http://airflow:8080/airflow",
            "AIRFLOW_USER": "admin",
            "AIRFLOW_PASSWORD": "admin",
            "PG_PASSWORD": "postgres",
            "SUPERSET_USER": "admin",
            "SUPERSET_PASSWORD": "admin",
        },
    )

    def catalog(role: str, password: str, apply) -> set[str]:
        with psycopg2.connect(_role_dsn(postgres_with_real_init_schema, role, password)) as conn:
            with conn.cursor() as cur:
                apply(cur)
                cur.execute("SELECT column_name FROM data_catalog WHERE dataset = %s", (f"combo_{suffix}",))
                return {row[0] for row in cur.fetchall()}

    def runs(role: str, password: str, apply) -> set[str]:
        with psycopg2.connect(_role_dsn(postgres_with_real_init_schema, role, password)) as conn:
            with conn.cursor() as cur:
                apply(cur)
                cur.execute("SELECT run_id FROM extraction_runs WHERE run_id IN (%s, %s)", (legacy, scoped))
                return {row[0] for row in cur.fetchall()}

    mcp = ("omega_mcp_infra", "test_omega_mcp_infra_password")
    assert catalog(*mcp, lambda cur: pipeline._set_db_scope(cur, None, None)) == {"legacy_col"}
    assert catalog(*mcp, lambda cur: pipeline._set_db_scope(cur, tenant, workspace)) == {"scoped_col"}
    assert catalog(*mcp, lambda cur: None) == set()
    cartridges = _load_service_module(monkeypatch, "mcp-infra", "app.tools.cartridges")
    admin_ctx = {"trusted": True, "role": "admin", "allowed_cartridges": ["*"]}
    scoped_ctx = {"trusted": True, "tenant_id": tenant, "workspace_id": workspace}
    assert catalog(*mcp, lambda cur: cartridges._set_pg_scope(cur, admin_ctx)) == {"legacy_col"}
    assert catalog(*mcp, lambda cur: cartridges._set_pg_scope(cur, scoped_ctx)) == {"scoped_col"}
    console = ("omega_console", OMEGA_CONSOLE_PASSWORD)
    assert runs(*console, lambda cur: cur.execute("SELECT set_config('app.platform_admin', 'true', true)")) == {legacy}
    assert runs(*console, lambda cur: None) == set()

    store = _load_service_module(monkeypatch, "refinement", "app.dataset_store")

    def columns(apply) -> set[str]:
        refinement_dsn = _role_dsn(
            postgres_with_real_init_schema, "omega_refinement", "test_omega_refinement_password"
        )
        with psycopg2.connect(refinement_dsn) as conn, conn.cursor() as cur:
            apply(cur)
            cur.execute("SELECT column_name FROM data_catalog WHERE dataset = %s", (f"combo_{suffix}",))
            return {row[0] for row in cur.fetchall()}

    assert columns(lambda cur: store._apply_scope(cur, None, None, platform_admin=True)) == {"legacy_col"}
    assert columns(lambda cur: store._apply_scope(cur, None, None, platform_admin=False)) == set()
    assert columns(lambda cur: store._apply_scope(cur, tenant, workspace)) == {"scoped_col"}


def _seed_runs(dsn: str, suffix: str) -> tuple[str, str]:
    async def seed() -> tuple[str, str]:
        admin = await asyncpg.connect(dsn)
        try:
            scopes = []
            for name in (f"logs-{suffix}", f"logs-other-{suffix}"):
                tenant = await admin.fetchval(
                    "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id::text", name
                )
                workspace = await admin.fetchval(
                    "INSERT INTO workspaces (tenant_id, name) VALUES ($1::uuid, $2) RETURNING id::text", tenant, name
                )
                scopes.append((tenant, workspace))
            (tenant, workspace), (other_tenant, other_workspace) = scopes
            await admin.execute(
                """INSERT INTO pipeline_runs (run_id, dag_id, cartridge_id, entity, status, tenant_id, workspace_id)
                   VALUES ($1, 'sap_b1_extract', 'sap_b1', 'OINV', 'success', $3::uuid, $4::uuid),
                          ($2, 'sap_b1_extract', 'sap_b1', 'OINV', 'failed', $5::uuid, $6::uuid),
                          ('platform-' || $1, 'omega_scheduler', 'platform', 'scheduler', 'success', NULL, NULL)""",
                f"logs-{suffix}",
                f"other-{suffix}",
                tenant,
                workspace,
                other_tenant,
                other_workspace,
            )
            await admin.execute(
                """INSERT INTO run_logs (run_id, cartridge, entity, message, scope_status, tenant_id, workspace_id)
                   VALUES ($1, 'sap_b1', 'OINV', 'legacy', 'legacy_unscoped', NULL, NULL),
                          ($1, 'sap_b1', 'OINV', 'scoped', 'scoped', $2::uuid, $3::uuid),
                          ($1, 'sap_b1', 'OINV', 'other', 'scoped', $4::uuid, $5::uuid)""",
                f"logs-{suffix}",
                tenant,
                workspace,
                other_tenant,
                other_workspace,
            )
        finally:
            await admin.close()
        return tenant, workspace

    return asyncio.run(seed())


def _load_mcp_gateway(monkeypatch, dsn: str):
    import importlib
    from urllib.parse import urlsplit

    from tests.test_mcp_domain_kpi_tools import SIGNING_KEY

    url = urlsplit(dsn)
    main = _load_service_module(
        monkeypatch,
        "mcp-infra",
        "app.main",
        {
            "APP_ENV": "test",
            "SECURITY_CONTEXT_SIGNING_KEY": SIGNING_KEY,
            "INTERNAL_API_KEY": "legacy_transport_key_64_chars_bbbbbbbbbbbbbbbbbbbbbbbbbb",
            "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA": "console_to_mcp_key_64_chars_cccccccccccccccccccccc",
            "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE": "mcp_to_console_key_64_chars_dddddddddddddddddddddd",
            "INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT": "mcp_to_refinement_key_64_chars_eeeeeeeeeeeeeeeeeee",
            "AIRFLOW_USER": "airflow",
            "AIRFLOW_PASSWORD": "airflow",
            "SUPERSET_USER": "admin",
            "SUPERSET_PASSWORD": "admin",
            "MINIO_SECRET_KEY": "miniosecret",
            "PG_HOST": str(url.hostname),
            "PG_PORT": str(url.port),
            "PG_DB": url.path.lstrip("/"),
            "PG_USER": "omega_mcp_infra",
            "PG_PASSWORD": "test_omega_mcp_infra_password",
        },
    )
    return main, importlib.import_module("app.tools.cartridges")


_BASE_CTX = {"trusted": True, "source": "console", "permissions": ["cartridges.read"]}
_ADMIN_CTX = {**_BASE_CTX, "role": "admin", "allowed_cartridges": ["*"]}
_DENIED_CTXS = (
    {**_BASE_CTX, "role": "analyst", "allowed_cartridges": ["*"]},
    {**_BASE_CTX, "role": "admin", "allowed_cartridges": ["sap_b1"]},
)
_TOOL_ONLY_CTXS = (None, {"trusted": True, "role": "analyst"}, {"trusted": True, "role": "admin", "allowed_cartridges": ["*"]})


def _run_tool(main, cartridges, tool: str, ctx: dict, args: dict):
    from tests.test_mcp_domain_kpi_tools import _signed

    req = main.InvokeRequest(tool=tool, args=dict(args), security_context=_signed(ctx))
    main._enforce_data_scope(req, "console")
    return getattr(cartridges, tool)(**req.args)


def _assert_gateway_denies(main, cartridges, tool: str, scoped: dict, args: dict) -> None:
    from fastapi import HTTPException

    attempts = [(ctx, args) for ctx in _DENIED_CTXS]
    attempts += [(scoped, {**args, key: value}) for key, value in (
        ("security_context", {**_ADMIN_CTX, "_unscoped_admin": True}),
        ("tenant_id", scoped["tenant_id"]),
        ("workspace_id", scoped["workspace_id"]),
    )]
    for ctx, call_args in attempts:
        with pytest.raises(HTTPException) as exc:
            _run_tool(main, cartridges, tool, ctx, call_args)
        assert exc.value.status_code == 403


def test_live_mcp_infra_run_logs_follow_the_gateway_scope(postgres_with_real_init_schema, monkeypatch):
    suffix = uuid.uuid4().hex[:10]
    run_id = f"logs-{suffix}"
    tenant, workspace = _seed_runs(postgres_with_real_init_schema, suffix)
    main, cartridges = _load_mcp_gateway(monkeypatch, postgres_with_real_init_schema)
    scoped = {**_BASE_CTX, "role": "analyst", "tenant_id": tenant, "workspace_id": workspace, "allowed_cartridges": ["sap_b1"]}

    def read(ctx: dict) -> set[str]:
        rows = _run_tool(main, cartridges, "cartridge_get_run_logs", ctx, {"run_id": run_id})
        return {row["message"] for row in rows}

    assert read(scoped) == {"scoped"}
    assert read(_ADMIN_CTX) == {"legacy"}
    _assert_gateway_denies(main, cartridges, "cartridge_get_run_logs", scoped, {"run_id": run_id})
    for ctx in _TOOL_ONLY_CTXS:
        assert cartridges.cartridge_get_run_logs(run_id, security_context=ctx) == []


def test_live_mcp_infra_job_status_follows_the_gateway_scope(postgres_with_real_init_schema, monkeypatch):
    from fastapi import HTTPException

    suffix = uuid.uuid4().hex[:10]
    run_id, platform_run = f"logs-{suffix}", f"platform-logs-{suffix}"
    tenant, workspace = _seed_runs(postgres_with_real_init_schema, suffix)
    main, cartridges = _load_mcp_gateway(monkeypatch, postgres_with_real_init_schema)
    scoped = {**_BASE_CTX, "role": "analyst", "tenant_id": tenant, "workspace_id": workspace, "allowed_cartridges": ["sap_b1"]}

    def status(ctx: dict, run: str) -> str | None:
        return _run_tool(main, cartridges, "cartridge_get_job_status", ctx, {"run_id": run}).get("status")

    assert status(scoped, run_id) == "success"
    with pytest.raises(HTTPException) as exc:
        status(scoped, f"other-{suffix}")
    assert exc.value.status_code == 403
    assert status(_ADMIN_CTX, platform_run) == "success"
    assert status(_ADMIN_CTX, run_id) is None
    _assert_gateway_denies(main, cartridges, "cartridge_get_job_status", scoped, {"run_id": run_id})
    for ctx in _TOOL_ONLY_CTXS:
        assert cartridges.cartridge_get_job_status(platform_run, security_context=ctx) == {
            "error": f"Run '{platform_run}' not found"
        }
