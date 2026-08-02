from __future__ import annotations

import uuid

import asyncpg
import pytest

from tests.test_control_room_live_postgres_operational_truth_pipeline import _seed_scope
from tests.test_operational_rls_console_refinement import (
    OMEGA_CONSOLE_PASSWORD,
    OMEGA_OUTCOME_BINDER_PASSWORD,
    OMEGA_REFINEMENT_PASSWORD,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    postgres_with_real_init_schema,
)


def _role_dsn(admin_dsn: str, role: str, password: str) -> str:
    return admin_dsn.replace(
        f"{POSTGRES_USER}:{POSTGRES_PASSWORD}", f"{role}:{password}"
    )


@pytest.mark.asyncio
async def test_silver_lineage_is_append_only_and_strictly_workspace_scoped(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope_a = await _seed_scope(admin, "lineage-final-a")
        scope_b = await _seed_scope(admin, "lineage-final-b")
        columns = {
            str(row["column_name"]): str(row["is_nullable"])
            for row in await admin.fetch(
                "SELECT column_name,is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='silver_lineage'"
            )
        }
        forced = await admin.fetchval(
            "SELECT relforcerowsecurity FROM pg_class "
            "WHERE oid='silver_lineage'::regclass"
        )
    finally:
        await admin.close()

    assert columns["tenant_id"] == "NO"
    assert columns["workspace_id"] == "NO"
    assert forced is True

    refinement_dsn = _role_dsn(
        postgres_with_real_init_schema,
        "omega_refinement",
        OMEGA_REFINEMENT_PASSWORD,
    )
    writer = await asyncpg.connect(refinement_dsn)
    try:
        async with writer.transaction():
            await writer.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_a["tenant_id"],
                scope_a["workspace_id"],
            )
            await writer.execute(
                """INSERT INTO silver_lineage(
                       silver_name,cartridge_id,source_entity,sql_def,layer,
                       row_count,storage_uri,tenant_id,workspace_id,scope_status,
                       materialization_run_id
                   ) VALUES($1,'acceptance','Employee','', 'silver',1,NULL,
                            $2,$3,'scoped',$4)""",
                "lineage_a",
                scope_a["tenant_id"],
                scope_a["workspace_id"],
                uuid.uuid4(),
            )
        async with writer.transaction():
            await writer.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_b["tenant_id"],
                scope_b["workspace_id"],
            )
            assert await writer.fetchval("SELECT count(*) FROM silver_lineage") == 0
            with pytest.raises(asyncpg.PostgresError):
                await writer.execute(
                    "UPDATE silver_lineage SET silver_name='mutated' WHERE silver_name='lineage_a'"
                )
    finally:
        await writer.close()


@pytest.mark.asyncio
async def test_operational_outcome_binding_requires_exact_scoped_registry_and_intelligence(
    postgres_with_real_init_schema: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        scope_a = await _seed_scope(admin, "outcome-binding-a")
        scope_b = await _seed_scope(admin, "outcome-binding-b")
        expected_run_id = "dataset_refresh_chain:scheduled__binding-a"
        expected_run_ref = (
            f"gold-refresh:{scope_a['workspace_id']}:acceptance:scheduled__binding-a"
        )
        await admin.execute(
            """INSERT INTO pipeline_runs(
                   run_id,dag_id,cartridge_id,entity,status,tenant_id,workspace_id
               ) VALUES($1,'dataset_refresh_chain','acceptance','DatasetRefreshChain',
                        'running',$2,$3)""",
            expected_run_id,
            scope_a["tenant_id"],
            scope_a["workspace_id"],
        )
        intelligence_id = await admin.fetchval(
            """INSERT INTO intelligence_runs(
                   run_ref,tenant_id,workspace_id,source_system,run_mode,status,
                   signals_generated,request
               ) VALUES($1,$2,$3,'acceptance','gold_refresh','running',0,
                        jsonb_build_object('datasets',jsonb_build_array('employee_360')))
               RETURNING id""",
            expected_run_ref,
            scope_a["tenant_id"],
            scope_a["workspace_id"],
        )
    finally:
        await admin.close()

    claims = (
        "employee_360",
        uuid.uuid4(),
        uuid.uuid4(),
        3,
        "version-1",
        "a" * 64,
        "b" * 64,
        "c" * 64,
    )
    console = await asyncpg.connect(
        _role_dsn(
            postgres_with_real_init_schema,
            "omega_console",
            OMEGA_CONSOLE_PASSWORD,
        )
    )
    try:
        async with console.transaction():
            await console.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_a["tenant_id"],
                scope_a["workspace_id"],
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await console.fetchval(
                    "SELECT stage_operational_outcome_binding("
                    "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    expected_run_id,
                    expected_run_ref,
                    intelligence_id,
                    *claims,
                )
    finally:
        await console.close()

    binder = await asyncpg.connect(
        _role_dsn(
            postgres_with_real_init_schema,
            "omega_outcome_binder",
            OMEGA_OUTCOME_BINDER_PASSWORD,
        )
    )
    try:
        async with binder.transaction():
            await binder.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_a["tenant_id"],
                scope_a["workspace_id"],
            )
            authority = await binder.fetchval(
                "SELECT stage_operational_outcome_binding("
                "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                expected_run_id,
                expected_run_ref,
                intelligence_id,
                *claims,
            )
            assert len(authority) == 64
        async with binder.transaction():
            await binder.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_b["tenant_id"],
                scope_b["workspace_id"],
            )
            with pytest.raises(asyncpg.PostgresError):
                await binder.fetchval(
                    "SELECT stage_operational_outcome_binding("
                    "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    expected_run_id,
                    expected_run_ref,
                    intelligence_id,
                    *claims,
                )
    finally:
        await binder.close()

    admin = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await admin.execute(
            "UPDATE intelligence_runs SET status='binding_pending',"
            "signals_generated=1 WHERE id=$1",
            intelligence_id,
        )
    finally:
        await admin.close()
    console = await asyncpg.connect(
        _role_dsn(
            postgres_with_real_init_schema, "omega_console", OMEGA_CONSOLE_PASSWORD
        )
    )
    try:
        async with console.transaction():
            await console.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_a["tenant_id"],
                scope_a["workspace_id"],
            )
            digest = await console.fetchval(
                "SELECT finalize_operational_outcome_binding($1,$2,$3)",
                expected_run_id,
                expected_run_ref,
                intelligence_id,
            )
            replay = await console.fetchval(
                "SELECT finalize_operational_outcome_binding($1,$2,$3)",
                expected_run_id,
                expected_run_ref,
                intelligence_id,
            )
            assert digest == replay and len(digest) == 64
            assert (
                await console.fetchval(
                    "SELECT count(*) FROM operational_outcome_bindings"
                )
                == 1
            )
        async with console.transaction():
            await console.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                scope_b["tenant_id"],
                scope_b["workspace_id"],
            )
            assert (
                await console.fetchval(
                    "SELECT count(*) FROM operational_outcome_bindings"
                )
                == 0
            )
    finally:
        await console.close()
