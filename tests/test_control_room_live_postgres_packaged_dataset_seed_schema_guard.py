"""Real PostgreSQL proofs for the workspace uniqueness schema guard."""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from app.services import seed_packaged_datasets as packaged_seed
from tests.test_control_room_live_postgres_packaged_dataset_seed import (
    _insert_dataset,
    _new_workspaces,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


_FIXTURE_IMPORTS = (omega_console_live_dsn, postgres_with_real_init_schema)


async def _set_workspace_name_schema(admin_dsn: str, schema_state: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "ALTER TABLE public.datasets "
            "DROP CONSTRAINT IF EXISTS datasets_workspace_name_key"
        )
        if schema_state in {"canonical", "deferred"}:
            deferrability = (
                " DEFERRABLE INITIALLY DEFERRED" if schema_state == "deferred" else ""
            )
            await conn.execute(
                "ALTER TABLE public.datasets "
                "ADD CONSTRAINT datasets_workspace_name_key "
                f"UNIQUE (workspace_id, name){deferrability}"
            )
    finally:
        await conn.close()


async def _rows_by_name(admin_dsn: str, name: str) -> list[dict[str, object]]:
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT name, layer, cartridge, sources, sql_def, description,
                   column_mapping, schedule, tenant_id::text, workspace_id::text
              FROM public.datasets
             WHERE name = $1
             ORDER BY workspace_id
            """,
            name,
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def _install_role_shadow_dataset_table(admin_dsn: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS omega_console CASCADE")
        await conn.execute("CREATE SCHEMA omega_console AUTHORIZATION omega_console")
        await conn.execute(
            "CREATE TABLE omega_console.datasets "
            "(LIKE public.datasets INCLUDING DEFAULTS)"
        )
        await conn.execute("ALTER TABLE omega_console.datasets OWNER TO omega_console")
    finally:
        await conn.close()


async def _drop_role_shadow_schema(admin_dsn: str) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS omega_console CASCADE")
    finally:
        await conn.close()


async def _table_row_count(admin_dsn: str, table: str, name: str) -> int:
    assert table in {"public.datasets", "omega_console.datasets"}
    conn = await asyncpg.connect(admin_dsn)
    try:
        return int(
            await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE name = $1",
                name,
            )
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("startup", "workspace"))
@pytest.mark.parametrize("schema_state", ("missing", "deferred"))
async def test_unsafe_workspace_name_schema_fails_before_any_write(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    tmp_path,
    monkeypatch,
    entrypoint: str,
    schema_state: str,
) -> None:
    scopes = await _new_workspaces(postgres_with_real_init_schema, 2)
    dataset_name = f"salesforce_schema_guard_{schema_state}_{entrypoint}"
    sql_path = tmp_path / f"{dataset_name}.sql"
    sql_path.write_text(
        f"-- {dataset_name} (gold) cartridge: salesforce\n"
        '-- sources: ["salesforce_source"]\n'
        "SELECT 'canonical'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        packaged_seed,
        "_dataset_files",
        lambda: {"salesforce": [sql_path]},
    )
    for index, scope in enumerate(scopes):
        await _insert_dataset(
            postgres_with_real_init_schema,
            scope,
            name=dataset_name,
            cartridge="salesforce",
            sql=f"SELECT 'workspace-{index}-before'",
            mapping={"workspace": str(index)},
            schedule=f"{index} 4 * * *",
        )

    before = await _rows_by_name(postgres_with_real_init_schema, dataset_name)
    assert len(before) == 2
    assert {json.loads(row["column_mapping"])["workspace"] for row in before} == {
        "0",
        "1",
    }
    await _set_workspace_name_schema(postgres_with_real_init_schema, schema_state)

    try:
        pool = await asyncpg.create_pool(
            omega_console_live_dsn,
            min_size=1,
            max_size=1,
        )
        try:
            with pytest.raises(
                RuntimeError,
                match="datasets_workspace_name_key is required",
            ):
                if entrypoint == "startup":
                    await packaged_seed.seed_packaged_datasets(pool)
                else:
                    await packaged_seed.seed_packaged_datasets_for_workspace(
                        pool,
                        tenant_id=scopes[1]["tenant_id"],
                        workspace_id=scopes[1]["id"],
                        cartridge_id="salesforce",
                    )
        finally:
            await pool.close()

        after = await _rows_by_name(postgres_with_real_init_schema, dataset_name)
        assert after == before
        assert {row["workspace_id"] for row in after} == {
            scope["id"] for scope in scopes
        }
    finally:
        await _set_workspace_name_schema(
            postgres_with_real_init_schema,
            "canonical",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("startup", "workspace"))
async def test_role_search_path_cannot_redirect_packaged_dataset_writes(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    tmp_path,
    monkeypatch,
    entrypoint: str,
) -> None:
    scopes = await _new_workspaces(postgres_with_real_init_schema, 2)
    dataset_name = f"salesforce_shadow_guard_{entrypoint}_{uuid.uuid4().hex}"
    sql_path = tmp_path / f"{dataset_name}.sql"
    sql_path.write_text(
        f"-- {dataset_name} (gold) cartridge: salesforce\n"
        '-- sources: ["salesforce_source"]\n'
        "SELECT 'canonical'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        packaged_seed,
        "_dataset_files",
        lambda: {"salesforce": [sql_path]},
    )
    await _install_role_shadow_dataset_table(postgres_with_real_init_schema)

    try:
        pool = await asyncpg.create_pool(
            omega_console_live_dsn,
            min_size=1,
            max_size=1,
        )
        try:
            if entrypoint == "startup":
                assert await packaged_seed.seed_packaged_datasets(pool) is None
            else:
                result = await packaged_seed.seed_packaged_datasets_for_workspace(
                    pool,
                    tenant_id=scopes[1]["tenant_id"],
                    workspace_id=scopes[1]["id"],
                    cartridge_id="salesforce",
                )
                assert result["status"] == "success"
                assert result["seeded_rows"] == 1
        finally:
            await pool.close()

        assert (
            await _table_row_count(
                postgres_with_real_init_schema,
                "omega_console.datasets",
                dataset_name,
            )
            == 0
        )
        rows = await _rows_by_name(postgres_with_real_init_schema, dataset_name)
        if entrypoint == "startup":
            assert {scope["id"] for scope in scopes} <= {
                row["workspace_id"] for row in rows
            }
        else:
            assert len(rows) == 1
            assert rows[0]["workspace_id"] == scopes[1]["id"]
        assert all(row["sql_def"] == sql_path.read_text() for row in rows)
    finally:
        await _drop_role_shadow_schema(postgres_with_real_init_schema)
