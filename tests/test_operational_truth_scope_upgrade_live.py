from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


REPO = Path(__file__).resolve().parents[1]
LINEAGE_MIGRATION = REPO / "infra/init/99zzf_silver_lineage_authority.sql"
OUTCOME_MIGRATION = REPO / "infra/init/99zzi_operational_outcome_binding.sql"


@pytest.mark.asyncio
async def test_lineage_upgrade_quarantines_history_and_reruns_without_drift(
    postgres_with_real_init_schema: str,
) -> None:
    database = f"lineage_final_{uuid.uuid4().hex[:12]}"
    control = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await control.execute(f'CREATE DATABASE "{database}"')
        base = postgres_with_real_init_schema.rsplit("/", 1)[0]
        upgrade = await asyncpg.connect(f"{base}/{database}")
        try:
            await upgrade.execute(
                """
                CREATE TABLE tenants(id UUID PRIMARY KEY);
                CREATE TABLE workspaces(id UUID PRIMARY KEY,tenant_id UUID NOT NULL);
                CREATE UNIQUE INDEX workspaces_tenant_id_id_idx
                  ON workspaces(tenant_id,id);
                CREATE TABLE schema_migrations(
                  id BIGSERIAL PRIMARY KEY,filename TEXT UNIQUE,
                  applied_at TIMESTAMPTZ
                );
                CREATE TABLE silver_lineage(
                  id BIGSERIAL PRIMARY KEY,silver_name TEXT NOT NULL,
                  cartridge_id TEXT NOT NULL,source_entity TEXT NOT NULL,
                  source_load_date DATE,source_batch_id TEXT,sql_def TEXT NOT NULL,
                  column_mapping JSONB DEFAULT '{}',layer TEXT DEFAULT 'silver',
                  row_count BIGINT,storage_uri TEXT,created_by TEXT DEFAULT 'llm',
                  created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE FUNCTION omega_rls_workspace_matches(UUID,UUID)
                RETURNS BOOLEAN LANGUAGE SQL STABLE AS $$
                  SELECT $1::text=current_setting('app.tenant_id',true)
                     AND $2::text=current_setting('app.workspace_id',true)
                $$;
                INSERT INTO silver_lineage(
                  silver_name,cartridge_id,source_entity,sql_def,storage_uri
                ) VALUES(
                  'legacy','acceptance','Raw','SELECT 1',
                  's3://lakehouse/tenant_id=fake/workspace_id=fake/data.parquet'
                );
                """
            )
            migration = LINEAGE_MIGRATION.read_text(encoding="utf-8")
            await upgrade.execute(migration)
            await upgrade.execute(migration)
            assert await upgrade.fetchval("SELECT count(*) FROM silver_lineage") == 0
            assert (
                await upgrade.fetchval(
                    "SELECT count(*) FROM omega_quarantine.silver_lineage_legacy"
                )
                == 1
            )
            assert (
                await upgrade.fetchval(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name='silver_lineage' "
                    "AND column_name IN ('tenant_id','workspace_id') "
                    "AND is_nullable='YES'"
                )
                == 0
            )
            assert (
                await upgrade.fetchval(
                    "SELECT count(*) FROM schema_migrations "
                    "WHERE filename='99zzf_silver_lineage_authority.sql'"
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


@pytest.mark.asyncio
async def test_authority_migrations_rerun_on_current_schema(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        before = await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename=ANY($1::text[])",
            [LINEAGE_MIGRATION.name, OUTCOME_MIGRATION.name],
        )
        await conn.execute(LINEAGE_MIGRATION.read_text(encoding="utf-8"))
        await conn.execute(OUTCOME_MIGRATION.read_text(encoding="utf-8"))
        after = await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename=ANY($1::text[])",
            [LINEAGE_MIGRATION.name, OUTCOME_MIGRATION.name],
        )
        assert before == after == 2
        assert (
            await conn.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='silver_lineage'::regclass"
            )
            is True
        )
        assert (
            await conn.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='operational_outcome_bindings'::regclass"
            )
            is True
        )
    finally:
        await conn.close()
