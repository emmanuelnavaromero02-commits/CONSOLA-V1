from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    OMEGA_CONSOLE_PASSWORD,
    OMEGA_REFINEMENT_PASSWORD,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    postgres_with_real_init_schema,
)


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra/init/99zzd_silver_lineage_scope.sql"
AIRFLOW_PASSWORD = "test_omega_airflow_dag_password"


def _role_dsn(dsn: str, role: str, password: str) -> str:
    return dsn.replace(f"{POSTGRES_USER}:{POSTGRES_PASSWORD}", f"{role}:{password}")


async def _scopes(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    result = []
    for label in ("a", "b"):
        suffix = uuid.uuid4().hex
        tenant = await conn.fetchval(
            "INSERT INTO tenants(name, slug) VALUES($1,$2) RETURNING id",
            f"lineage-{label}-{suffix}",
            f"lineage-{label}-{suffix}",
        )
        workspace = await conn.fetchval(
            "INSERT INTO workspaces(tenant_id,name) VALUES($1,$2) RETURNING id",
            tenant,
            f"Lineage {label} {suffix}",
        )
        result.append((str(tenant), str(workspace)))
    return result


async def _insert_lineage(
    dsn: str, scope: tuple[str, str], name: str, *, row_scope=None
) -> None:
    tenant_id, workspace_id = scope
    row_tenant, row_workspace = row_scope or scope
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true), "
                "set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            await conn.execute(
                """
                INSERT INTO silver_lineage(
                    silver_name,cartridge_id,source_entity,sql_def,layer,
                    row_count,storage_uri,tenant_id,workspace_id,scope_status
                ) VALUES($1,'replicon','raw/replicon/Probe','SELECT 1',
                         'silver',1,$2,$3,$4,'scoped')
                """,
                name,
                f"s3://lakehouse/silver/{name}.parquet",
                row_tenant,
                row_workspace,
            )
    finally:
        await conn.close()


async def _insert_catalog(dsn: str, scope: tuple[str, str]) -> None:
    tenant_id, workspace_id = scope
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true), "
                "set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            await conn.execute(
                """
                INSERT INTO data_catalog(
                    dataset,layer,cartridge,column_name,data_type,
                    tenant_id,workspace_id,scope_status
                ) VALUES('pnl_mensual','gold','replicon','project_id','VARCHAR',
                         $1,$2,'scoped')
                """,
                tenant_id,
                workspace_id,
            )
    finally:
        await conn.close()


async def _visible(dsn: str, scope: tuple[str, str] | None) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            if scope:
                await conn.execute(
                    "SELECT set_config('app.tenant_id',$1,true), "
                    "set_config('app.workspace_id',$2,true)",
                    *scope,
                )
            rows = await conn.fetch(
                "SELECT silver_name FROM silver_lineage ORDER BY silver_name"
            )
            return [str(row["silver_name"]) for row in rows]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_lineage_isolation_and_runtime_role_grants(
    postgres_with_real_init_schema: str,
):
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope_a, scope_b = await _scopes(admin)
        forced = await admin.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid='silver_lineage'::regclass"
        )
        bypass = await admin.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname='omega_airflow_dag'"
        )
    finally:
        await admin.close()

    refinement = _role_dsn(
        postgres_with_real_init_schema, "omega_refinement", OMEGA_REFINEMENT_PASSWORD
    )
    console = _role_dsn(
        postgres_with_real_init_schema, "omega_console", OMEGA_CONSOLE_PASSWORD
    )
    airflow = _role_dsn(
        postgres_with_real_init_schema, "omega_airflow_dag", AIRFLOW_PASSWORD
    )
    await _insert_lineage(refinement, scope_a, "lineage_a")
    await _insert_lineage(refinement, scope_b, "lineage_b")
    await _insert_catalog(refinement, scope_a)
    await _insert_catalog(refinement, scope_b)

    assert forced is True
    assert bypass is False
    assert await _visible(refinement, None) == []
    assert await _visible(console, scope_a) == ["lineage_a"]
    assert await _visible(console, scope_b) == ["lineage_b"]
    with pytest.raises(asyncpg.PostgresError):
        await _insert_lineage(refinement, scope_a, "cross_scope", row_scope=scope_b)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _visible(airflow, scope_a)


@pytest.mark.asyncio
async def test_upgrade_quarantines_unscoped_history_and_is_idempotent(
    postgres_with_real_init_schema: str,
):
    database = f"lineage_upgrade_{uuid.uuid4().hex[:12]}"
    control = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await control.execute(f'CREATE DATABASE "{database}"')
        base = postgres_with_real_init_schema.rsplit("/", 1)[0]
        upgrade = await asyncpg.connect(f"{base}/{database}")
        try:
            await upgrade.execute(
                """
                CREATE TABLE tenants(id UUID PRIMARY KEY);
                CREATE TABLE workspaces(id UUID PRIMARY KEY, tenant_id UUID NOT NULL);
                CREATE UNIQUE INDEX workspaces_tenant_id_id_idx ON workspaces(tenant_id,id);
                CREATE TABLE schema_migrations(
                    id BIGSERIAL PRIMARY KEY, filename TEXT UNIQUE, applied_at TIMESTAMPTZ
                );
                CREATE TABLE silver_lineage(
                    id BIGSERIAL PRIMARY KEY, silver_name TEXT NOT NULL,
                    cartridge_id TEXT NOT NULL, source_entity TEXT NOT NULL,
                    source_load_date DATE, source_batch_id TEXT, sql_def TEXT NOT NULL,
                    column_mapping JSONB DEFAULT '{}', layer TEXT DEFAULT 'silver',
                    row_count BIGINT, storage_uri TEXT, created_by TEXT DEFAULT 'llm',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE FUNCTION omega_rls_workspace_matches(UUID,UUID) RETURNS BOOLEAN
                LANGUAGE SQL STABLE AS $$ SELECT $1::text=current_setting('app.tenant_id',true)
                    AND $2::text=current_setting('app.workspace_id',true) $$;
                INSERT INTO silver_lineage(
                    silver_name,cartridge_id,source_entity,sql_def,storage_uri
                ) VALUES('legacy','replicon','raw/replicon/Probe','SELECT 1',
                         's3://lakehouse/tenant_id=fake/workspace_id=fake/data.parquet');
                """
            )
            sql = MIGRATION.read_text(encoding="utf-8")
            await upgrade.execute(sql)
            assert await upgrade.fetchval("SELECT count(*) FROM silver_lineage") == 0
            assert (
                await upgrade.fetchval(
                    "SELECT count(*) FROM omega_quarantine.silver_lineage_legacy"
                )
                == 1
            )
            nullable = await upgrade.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='silver_lineage' "
                "AND column_name IN ('tenant_id','workspace_id') AND is_nullable='YES'"
            )
            assert nullable == 0
            await upgrade.execute(sql)
            assert (
                await upgrade.fetchval(
                    "SELECT count(*) FROM omega_quarantine.silver_lineage_legacy"
                )
                == 1
            )
        finally:
            await upgrade.close()
    finally:
        await control.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=$1 AND pid<>pg_backend_pid()",
            database,
        )
        await control.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await control.close()
