from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

import asyncpg
import duckdb
import pandas as pd
import pytest
import yaml
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)
from tests.replicon_wip_live_harness import RUNTIME_SCRIPT
from tests.test_operational_truth_data_kb_config import _packaged
from tests.test_replicon_wip_v3_currency import _resolved_sql, _write_inputs


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/init/99zp_replicon_wip_materialization_v3.sql"


async def _wait_for_migration(conn: asyncpg.Connection) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if await conn.fetchval(
                "SELECT 1 FROM schema_migrations WHERE filename=$1", MIGRATION.name
            ):
                return
        except asyncpg.PostgresError:
            pass
        await asyncio.sleep(0.5)
    raise AssertionError("Replicon v3 migration did not finish")


def _legacy_packaged_sql() -> str:
    raw = subprocess.check_output(
        [
            "git",
            "show",
            "49f792eedcf1177d8e7681478c5efecdc43e908b:cartridges/replicon/app/config/knowledge_bits.yaml",
        ],
        cwd=ROOT,
        text=True,
    )
    payload = yaml.safe_load(raw)
    sql = next(
        item["sql"]
        for item in payload["knowledge_bits"]
        if item.get("id") == "kb_wip_mensual"
    )
    assert "COALESCE(fx.mxn_to_usd, 0.05)" in sql
    return sql


@pytest.mark.asyncio
async def test_real_legacy_parquet_is_quarantined_and_only_v3_run_is_public(
    postgres_with_real_init_schema: str,
    tmp_path: Path,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    await _wait_for_migration(conn)
    legacy_sql = _legacy_packaged_sql()
    legacy_path = tmp_path / "customer/path/legacy_wip.parquet"
    legacy_path.parent.mkdir(parents=True)
    pd.DataFrame({"amount_usd": [50.0]}).to_parquet(legacy_path, index=False)
    assert duckdb.connect().execute(
        "SELECT amount_usd FROM read_parquet(?)", [str(legacy_path)]
    ).fetchone() == (50.0,)

    await conn.execute(
        """CREATE SCHEMA IF NOT EXISTS knowledge_bits;
           DROP VIEW IF EXISTS knowledge_bits.customer_table;
           DROP TABLE IF EXISTS knowledge_bits.customer_table;
           DROP TABLE IF EXISTS knowledge_bits_quarantine.customer_table;
           CREATE TABLE knowledge_bits.customer_table(amount_usd DOUBLE PRECISION);
           INSERT INTO knowledge_bits.customer_table VALUES (50.0);"""
    )
    await conn.execute(
        """INSERT INTO kb_config
             (cartridge_id, kb_id, name, description, sql, pg_table,
              output_path, enabled, materialization_status)
           VALUES ('replicon', 'kb_wip_mensual', 'Customer name',
                   'Customer description', $1, 'customer_table',
                   'customer/path', TRUE, 'current')
           ON CONFLICT (cartridge_id, kb_id) DO UPDATE SET
             name=EXCLUDED.name, description=EXCLUDED.description,
             sql=EXCLUDED.sql, pg_table=EXCLUDED.pg_table,
             output_path=EXCLUDED.output_path, enabled=EXCLUDED.enabled,
             package_version=NULL, package_sql_digest=NULL,
             materialization_status=EXCLUDED.materialization_status""",
        legacy_sql,
    )
    await conn.execute(
        """INSERT INTO kb_runs(run_id, kb_id, status, cartridge_id, artifact_status)
           VALUES ('legacy-wip-run', 'kb_wip_mensual', 'completed',
                   'replicon', 'current')
           ON CONFLICT (run_id) DO UPDATE SET artifact_status='current'"""
    )
    await conn.execute(MIGRATION.read_text(encoding="utf-8"))
    assert (
        await conn.fetchval("SELECT to_regclass('knowledge_bits.customer_table')")
        is None
    )
    assert (
        await conn.fetchval(
            "SELECT to_regclass('knowledge_bits_quarantine.customer_table')"
        )
        == "knowledge_bits_quarantine.customer_table"
    )
    assert dict(
        await conn.fetchrow(
            "SELECT artifact_status, invalid_reason FROM kb_runs WHERE run_id='legacy-wip-run'"
        )
    ) == {
        "artifact_status": "legacy_invalid",
        "invalid_reason": "invalid_legacy_fx",
    }

    tenant, workspace, user_id = await conn.fetchrow(
        """WITH tenant AS (
               INSERT INTO tenants(name,slug) VALUES ('WIP tenant A','wip-tenant-a') RETURNING id
             ), workspace AS (INSERT INTO workspaces(tenant_id,name) SELECT id,'WIP workspace A' FROM tenant RETURNING id),
             app_user AS (INSERT INTO users(email,name,password_hash,tenant_id) SELECT 'wip-live-a@example.test','WIP live A','test-only',id FROM tenant RETURNING id
             )
             SELECT tenant.id,workspace.id,app_user.id FROM tenant,workspace,app_user"""
    )
    await conn.execute(
        """INSERT INTO replicon_base_currency_config
             (tenant_id, workspace_id, effective_from, effective_to, currency,
              authority_source, verified_by)
           VALUES
             ($1,$2,'2026-01-01','2026-02-01','USD','live-test',$3),
             ($1,$2,'2026-02-01','2026-04-01','MXN','live-test',$3),
             ($1,$2,'2026-04-01','2026-05-01','EUR','live-test',$3),
             ($1,$2,'2026-06-01',NULL,'ZZZ','live-test',$3)""",
        tenant,
        workspace,
        user_id,
    )
    definitions = _packaged()["kb_wip_mensual"]
    canonical_path = tmp_path / "current.sql"
    resolved_path = tmp_path / "resolved.sql"
    canonical_path.write_text(definitions["sql"], encoding="utf-8")
    resolved_path.write_text(
        _resolved_sql(definitions["sql"], _write_inputs(tmp_path)), encoding="utf-8"
    )
    artifact_path = tmp_path / "current.parquet"
    result_path = tmp_path / "runtime-result.json"
    role_dsn = postgres_with_real_init_schema.replace(
        "postgres:test_postgres_password",
        "omega_cartridge_replicon:test_omega_cartridge_replicon_password",
    )
    env = {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'cartridges/replicon'}:{ROOT}",
        "DATABASE_URL": role_dsn,
        "MINIO_ACCESS_KEY": "live-test",
        "MINIO_SECRET_KEY": "live-test",
        "SECURITY_CONTEXT_SIGNING_KEY": "live-test-signing-key-at-least-32-bytes",
        "TENANT_ID": str(tenant),
        "WORKSPACE_ID": str(workspace),
        "CANONICAL_SQL": str(canonical_path),
        "RESOLVED_SQL": str(resolved_path),
        "ARTIFACT_PATH": str(artifact_path),
        "RESULT_JSON": str(result_path),
    }
    subprocess.run(
        [sys.executable, "-c", RUNTIME_SCRIPT], cwd=ROOT, env=env, check=True
    )
    result = json.loads(result_path.read_text())
    assert duckdb.connect().execute(
        "SELECT COUNT(*) FROM read_parquet(?)", [str(artifact_path)]
    ).fetchone() == (result["rows"],)
    assert (
        await conn.fetchval("SELECT COUNT(*) FROM knowledge_bits.customer_table")
        == result["rows"]
    )
    assert not await conn.fetchval(
        "SELECT BOOL_OR(projectname='STALE-PACKAGE-RACE') FROM knowledge_bits.customer_table"
    )
    head = await conn.fetchval(
        """SELECT current_run_id FROM kb_materialization_heads
            WHERE cartridge_id='replicon' AND kb_id='kb_wip_mensual'
              AND tenant_id=$1 AND workspace_id=$2""",
        tenant,
        workspace,
    )
    assert head == result["current_run"]
    assert (
        await conn.fetchval(
            "SELECT artifact_status FROM kb_runs WHERE run_id=$1", result["stale_run"]
        )
        == "quarantined"
    )

    tenant_b = await conn.fetchval(
        "INSERT INTO tenants(name,slug) VALUES ('WIP tenant B','wip-tenant-b') RETURNING id"
    )
    workspace_b = await conn.fetchval(
        "INSERT INTO workspaces(tenant_id,name) VALUES ($1,'WIP workspace B') RETURNING id",
        tenant_b,
    )
    await conn.execute(
        """WITH run_row AS (INSERT INTO kb_runs
             (run_id,cartridge_id,kb_id,status,tenant_id,workspace_id,scope_status,
              package_version,sql_digest,input_digest,generation,artifact_status)
           VALUES ('wip-tenant-b-current','replicon','kb_wip_mensual','completed',
                   $1,$2,'scoped',$3,$4,$5,1,'current') RETURNING run_id),
           head_row AS (INSERT INTO kb_materialization_heads
             (cartridge_id,kb_id,tenant_id,workspace_id,current_run_id,
              package_version,sql_digest,input_digest,generation,state)
           VALUES ('replicon','kb_wip_mensual',$1,$2,'wip-tenant-b-current',
                   $3,$4,$5,1,'current') RETURNING current_run_id),
           currency_row AS (INSERT INTO replicon_base_currency_config
             (tenant_id,workspace_id,effective_from,currency,authority_source,verified_by)
           VALUES ($1,$2,'2026-01-01','EUR','live-test',$6) RETURNING currency)
           INSERT INTO knowledge_bits_history.customer_table
             (projectname,tenant_id,workspace_id,kb_run_id,package_version,
              sql_digest,input_digest)
           SELECT 'TENANT-B',$1::text,$2::text,head_row.current_run_id,$3,$4,$5
           FROM head_row,currency_row""",
        tenant_b,
        workspace_b,
        result["package_version"],
        result["sql_digest"],
        result["input_digest"],
        user_id,
    )
    role = await asyncpg.connect(role_dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await role.fetchval(
                "SELECT COUNT(*) FROM knowledge_bits_quarantine.customer_table"
            )
        for scoped_tenant, scoped_workspace, expected_currency, sees_tenant_b in (
            (tenant, workspace, "USD", False),
            (tenant_b, workspace_b, "EUR", True),
        ):
            await role.execute(
                "SELECT set_config('app.tenant_id',$1,false), "
                "set_config('app.workspace_id',$2,false)",
                str(scoped_tenant),
                str(scoped_workspace),
            )
            assert (
                await role.fetchval("SELECT COUNT(*) FROM kb_materialization_heads")
                == 1
            )
            assert await role.fetchval(
                "SELECT EXISTS(SELECT 1 FROM replicon_base_currency_config "
                "WHERE currency=$1)",
                expected_currency,
            )
            labels = await role.fetch(
                "SELECT projectname FROM knowledge_bits.customer_table"
            )
            assert (
                "TENANT-B" in {row["projectname"] for row in labels}
            ) is sees_tenant_b
            history_labels = await role.fetch(
                "SELECT projectname FROM knowledge_bits_history.customer_table"
            )
            assert (
                "TENANT-B" in {row["projectname"] for row in history_labels}
            ) is sees_tenant_b
        await role.execute(
            "SELECT set_config('app.tenant_id','',false), "
            "set_config('app.workspace_id','',false)"
        )
        assert await role.fetchval("SELECT COUNT(*) FROM kb_materialization_heads") == 0
        assert (
            await role.fetchval("SELECT COUNT(*) FROM knowledge_bits.customer_table")
            == 0
        )
        assert (
            await role.fetchval(
                "SELECT COUNT(*) FROM knowledge_bits_history.customer_table"
            )
            == 0
        )
    finally:
        await role.close()
    guard = runpy.run_path(str(ROOT / "mcp-infra/app/operational_truth.py"))[
        "replicon_artifact_block_reason"
    ]
    assert guard(
        "SELECT * FROM read_parquet('s3://bucket/customer/path/legacy_wip.parquet')",
        cartridge_id="replicon",
        reserved_markers=["customer/path", "customer_table"],
    )
    await conn.close()
