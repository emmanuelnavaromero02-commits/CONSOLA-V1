from __future__ import annotations

import io
import platform
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    POSTGRES_DB,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    _docker,
    _init_pgoptions,
    _mapped_postgres_port,
    _remove_test_container,
    _wait_for_schema,
    postgres_with_real_init_schema,
)


REPO = Path(__file__).resolve().parents[1]
LINEAGE_MIGRATION = REPO / "infra/init/99zzn_silver_lineage_authority.sql"
OUTCOME_MIGRATION = REPO / "infra/init/99zzr_operational_outcome_binding.sql"
SCHEDULE_LEASE_MIGRATION = REPO / "infra/init/99zzp_agent_schedule_effect_leases.sql"
SCHEDULE_FENCE_MIGRATION = REPO / "infra/init/99zzq_scheduled_effect_fencing.sql"
PR555_MIGRATION_MAPPING = {
    "99zz_runtime_hmac_nonces.sql": "99zzl_runtime_hmac_nonces.sql",
    "99zza_materialization_run_leases.sql": "99zzm_materialization_run_leases.sql",
    "99zzf_silver_lineage_authority.sql": "99zzn_silver_lineage_authority.sql",
    "99zzg_runtime_hmac_authority.sql": "99zzo_runtime_hmac_authority.sql",
    "99zzh_scheduled_effect_fencing.sql": "99zzq_scheduled_effect_fencing.sql",
    "99zzi_operational_outcome_binding.sql": "99zzr_operational_outcome_binding.sql",
}
PR555_NEW_MIGRATIONS = tuple(
    REPO / "infra/init" / name
    for name in (
        "99zzl_runtime_hmac_nonces.sql",
        "99zzm_materialization_run_leases.sql",
        "99zzn_silver_lineage_authority.sql",
        "99zzo_runtime_hmac_authority.sql",
        "99zzp_agent_schedule_effect_leases.sql",
        "99zzq_scheduled_effect_fencing.sql",
        "99zzr_operational_outcome_binding.sql",
    )
)
PR555_INITIAL_HEAD = "c680f0ddbd91f114797e70d0d26c5245d3cdc2db"


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
                    "WHERE filename='99zzn_silver_lineage_authority.sql'"
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
async def test_authority_migrations_upgrade_exact_historical_tree_and_rerun(
    postgres_with_real_init_schema: str,
    tmp_path: Path,
) -> None:
    del postgres_with_real_init_schema
    history_root = tmp_path
    if platform.system() == "Darwin":
        history_root = Path(tempfile.mkdtemp(prefix="pr555-upgrade-", dir=REPO.parent))
    archive = subprocess.run(
        ["git", "archive", PR555_INITIAL_HEAD, "infra/init"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(history_root, filter="data")
    container = f"pr555-historical-upgrade-{uuid.uuid4().hex[:12]}"
    result = _docker(
        "run",
        "--pull=never",
        "-d",
        "--name",
        container,
        "-e",
        f"POSTGRES_DB={POSTGRES_DB}",
        "-e",
        f"POSTGRES_USER={POSTGRES_USER}",
        "-e",
        f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
        "-e",
        f"PGOPTIONS={_init_pgoptions()}",
        "-v",
        f"{history_root / 'infra/init'}:/docker-entrypoint-initdb.d:ro",
        "-P",
        POSTGRES_IMAGE,
    )
    container_id = result.stdout.strip()
    try:
        port = _mapped_postgres_port(container_id)
        dsn = (
            f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
            f"@127.0.0.1:{port}/{POSTGRES_DB}"
        )
        await _wait_for_schema(dsn, container_id)
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval(
                "SELECT count(*) FROM schema_migrations "
                "WHERE filename=ANY($1::text[])",
                list(PR555_MIGRATION_MAPPING),
            ) == len(PR555_MIGRATION_MAPPING)
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM schema_migrations "
                    "WHERE filename=ANY($1::text[])",
                    [path.name for path in PR555_NEW_MIGRATIONS],
                )
                == 0
            )
            for migration in (*PR555_NEW_MIGRATIONS, *PR555_NEW_MIGRATIONS):
                await conn.execute(migration.read_text(encoding="utf-8"))
            assert await conn.fetchval(
                "SELECT count(*) FROM schema_migrations "
                "WHERE filename=ANY($1::text[])",
                [path.name for path in PR555_NEW_MIGRATIONS],
            ) == len(PR555_NEW_MIGRATIONS)
            assert await conn.fetchval(
                "SELECT count(*) FROM schema_migrations "
                "WHERE filename=ANY($1::text[])",
                list(PR555_MIGRATION_MAPPING),
            ) == len(PR555_MIGRATION_MAPPING)
            assert await conn.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='silver_lineage'::regclass"
            )
            assert await conn.fetchval(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='operational_outcome_bindings'::regclass"
            )
        finally:
            await conn.close()
    finally:
        _remove_test_container(container_id)
        if history_root != tmp_path:
            shutil.rmtree(history_root)


@pytest.mark.asyncio
async def test_legacy_schedule_schema_upgrades_before_fencing_and_reruns(
    postgres_with_real_init_schema: str,
) -> None:
    database = f"schedule_fence_upgrade_{uuid.uuid4().hex[:12]}"
    control = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await control.execute(f'CREATE DATABASE "{database}"')
        base = postgres_with_real_init_schema.rsplit("/", 1)[0]
        upgrade = await asyncpg.connect(f"{base}/{database}")
        try:
            await upgrade.execute(
                """
                DO $$ BEGIN
                  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='omega_console')
                    THEN CREATE ROLE omega_console NOBYPASSRLS; END IF;
                  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='omega_mcp_infra')
                    THEN CREATE ROLE omega_mcp_infra NOBYPASSRLS; END IF;
                END $$;
                CREATE TABLE schema_migrations(
                  id BIGSERIAL PRIMARY KEY,filename TEXT UNIQUE,
                  applied_at TIMESTAMPTZ
                );
                CREATE TABLE agent_schedule_runs(
                  id BIGSERIAL PRIMARY KEY,tenant_id UUID NOT NULL,
                  workspace_id UUID NOT NULL,agent_id UUID NOT NULL,
                  status TEXT NOT NULL DEFAULT 'running'
                );
                CREATE FUNCTION omega_rls_workspace_matches(UUID,UUID)
                RETURNS BOOLEAN LANGUAGE SQL STABLE AS $$
                  SELECT $1::text=current_setting('app.tenant_id',true)
                     AND $2::text=current_setting('app.workspace_id',true)
                $$;
                """
            )
            lease_sql = SCHEDULE_LEASE_MIGRATION.read_text(encoding="utf-8")
            fence_sql = SCHEDULE_FENCE_MIGRATION.read_text(encoding="utf-8")
            for _ in range(2):
                await upgrade.execute(lease_sql)
                await upgrade.execute(fence_sql)
            columns = await upgrade.fetch(
                """SELECT column_name FROM information_schema.columns
                     WHERE table_name='agent_schedule_runs'
                       AND column_name=ANY($1::text[]) ORDER BY column_name""",
                ["heartbeat_at", "lease_expires_at", "fencing_token"],
            )
            assert [row["column_name"] for row in columns] == [
                "fencing_token",
                "heartbeat_at",
                "lease_expires_at",
            ]
            assert (
                await upgrade.fetchval(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid='agent_schedule_runs'::regclass"
                )
                is True
            )
            assert (
                await upgrade.fetchval(
                    "SELECT count(*) FROM schema_migrations WHERE filename=ANY($1::text[])",
                    [SCHEDULE_LEASE_MIGRATION.name, SCHEDULE_FENCE_MIGRATION.name],
                )
                == 2
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
