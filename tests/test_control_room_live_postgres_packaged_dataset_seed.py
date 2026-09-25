"""Real PostgreSQL proofs for non-destructive packaged dataset reconciliation."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.services import seed_packaged_datasets as packaged_seed
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


_FIXTURE_IMPORTS = (omega_console_live_dsn, postgres_with_real_init_schema)
REPO_ROOT = Path(__file__).resolve().parents[1]


async def _new_workspaces(admin_dsn: str, count: int) -> list[dict[str, str]]:
    conn = await asyncpg.connect(admin_dsn)
    scopes: list[dict[str, str]] = []
    try:
        for index in range(count):
            suffix = uuid.uuid4().hex
            tenant_id = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"Packaged Seed Tenant {index} {suffix}",
                f"packaged-seed-{index}-{suffix}",
            )
            workspace_id = await conn.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
                tenant_id,
                f"Packaged Seed Workspace {index} {suffix}",
            )
            scopes.append({"tenant_id": str(tenant_id), "id": str(workspace_id)})
        return scopes
    finally:
        await conn.close()


async def _insert_dataset(
    admin_dsn: str,
    scope: dict[str, str],
    *,
    name: str,
    cartridge: str,
    sql: str,
    mapping: dict[str, str] | None = None,
    schedule: str | None = None,
) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            """
            INSERT INTO datasets (
                name, layer, cartridge, sources, sql_def, description,
                column_mapping, schedule, tenant_id, workspace_id
            )
            VALUES (
                $1, 'gold', $2, '[]'::jsonb, $3, 'installed before seed',
                $4::jsonb, $5, $6::uuid, $7::uuid
            )
            """,
            name,
            cartridge,
            sql,
            json.dumps(mapping or {}),
            schedule,
            scope["tenant_id"],
            scope["id"],
        )
    finally:
        await conn.close()


async def _rows(
    admin_dsn: str,
    workspace_ids: list[str],
) -> list[dict[str, object]]:
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT name, layer, cartridge, sources, sql_def, description,
                   column_mapping, schedule, tenant_id::text, workspace_id::text
              FROM datasets
             WHERE workspace_id = ANY($1::uuid[])
             ORDER BY workspace_id, name
            """,
            workspace_ids,
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def _run_manifest(
    console_dsn: str,
    packaged: dict[str, list[Path]],
    scopes: list[dict[str, str]],
) -> dict[str, int]:
    pool = await asyncpg.create_pool(console_dsn, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn, conn.transaction():
            return await packaged_seed._seed_packaged_dataset_rows(
                conn,
                packaged=packaged,
                target_workspaces=scopes,
                has_tenant_id=True,
            )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_complete_catalog_is_idempotent_isolated_and_non_destructive(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch,
) -> None:
    registry = REPO_ROOT / "cartridges"
    monkeypatch.setattr(packaged_seed, "_REGISTRY", registry)
    packaged = packaged_seed._dataset_files()
    scopes = await _new_workspaces(postgres_with_real_init_schema, 2)
    workspace_ids = [scope["id"] for scope in scopes]
    custom_name = "salesforce_custom_valid"
    custom_mapping = {"external": "internal"}
    custom_schedule = "15 3 * * *"
    salesforce_path = packaged["salesforce"][0]
    canonical = packaged_seed._parse_dataset(salesforce_path)
    canonical_by_name = {}
    for paths in packaged.values():
        for path in paths:
            dataset = packaged_seed._parse_dataset(path)
            canonical_by_name[dataset["name"]] = dataset

    # The sealed size of the catalog lives in one place (the seeder's expected
    # constants, which dataset_files() has just verified byte for byte); the
    # counts below follow it instead of repeating a number per cartridge added.
    total = packaged_seed._EXPECTED_CATALOG_FILES
    assert len(packaged) == 10
    assert "sap_b1" in packaged
    assert sum(len(paths) for paths in packaged.values()) == total
    assert len(canonical_by_name) == total, "dataset names must be unique across cartridges"
    for scope in scopes:
        await _insert_dataset(
            postgres_with_real_init_schema,
            scope,
            name=custom_name,
            cartridge="salesforce",
            sql="SELECT 'custom-valid'",
            mapping={"custom": "kept"},
            schedule="0 4 * * *",
        )
    await _insert_dataset(
        postgres_with_real_init_schema,
        scopes[0],
        name=canonical["name"],
        cartridge="salesforce",
        sql="SELECT 'obsolete'",
        mapping=custom_mapping,
        schedule=custom_schedule,
    )

    seeded = await _run_manifest(omega_console_live_dsn, packaged, scopes)
    first = await _rows(postgres_with_real_init_schema, workspace_ids)
    second_seeded = await _run_manifest(omega_console_live_dsn, packaged, scopes)
    second = await _rows(postgres_with_real_init_schema, workspace_ids)

    assert seeded == second_seeded
    assert set(seeded) == set(packaged)
    assert sum(seeded.values()) == 2 * total
    assert first == second
    for scope in scopes:
        scoped = [row for row in second if row["workspace_id"] == scope["id"]]
        assert len(scoped) == total + 1  # the whole catalog plus the custom row
        assert len({row["name"] for row in scoped}) == total + 1
        assert {row["cartridge"] for row in scoped} == set(packaged)
        assert all(row["tenant_id"] == scope["tenant_id"] for row in scoped)
        custom = next(row for row in scoped if row["name"] == custom_name)
        assert custom["sql_def"] == "SELECT 'custom-valid'"
        assert custom["column_mapping"] == '{"custom": "kept"}'
        assert custom["schedule"] == "0 4 * * *"
        for row in scoped:
            if row["name"] == custom_name:
                continue
            expected = canonical_by_name[row["name"]]
            sources = (
                json.loads(row["sources"])
                if isinstance(row["sources"], str)
                else row["sources"]
            )
            assert (row["layer"], row["cartridge"], sources) == (
                expected["layer"],
                expected["cartridge"],
                expected["sources"],
            )
            assert (row["sql_def"], row["description"]) == (
                expected["sql"],
                expected["description"],
            )
    refreshed = next(
        row
        for row in second
        if row["workspace_id"] == scopes[0]["id"] and row["name"] == canonical["name"]
    )
    assert refreshed["sql_def"] == canonical["sql"]
    assert refreshed["column_mapping"] == json.dumps(custom_mapping)
    assert refreshed["schedule"] == custom_schedule


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_file", "invalid_sources"])
async def test_incomplete_manifest_aborts_without_any_partial_write(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    tmp_path: Path,
    failure: str,
) -> None:
    scope = (await _new_workspaces(postgres_with_real_init_schema, 1))[0]
    good = tmp_path / "salesforce_good_packaged.sql"
    good.write_text(
        "-- salesforce_good_packaged (gold) cartridge: salesforce\n"
        '-- sources: ["salesforce_source"]\n'
        "SELECT 'new-packaged-sql'\n",
        encoding="utf-8",
    )
    broken = tmp_path / "salesforce_parse_failed.sql"
    if failure == "invalid_sources":
        broken.write_text(
            "-- salesforce_parse_failed (gold) cartridge: salesforce\n"
            "-- sources: [not-valid-json\n"
            "SELECT 2\n",
            encoding="utf-8",
        )
    for name, sql in (
        ("salesforce_good_packaged", "SELECT 'old-packaged-sql'"),
        ("salesforce_custom_valid", "SELECT 'custom-valid'"),
        ("salesforce_parse_failed", "SELECT 'installed-before-failure'"),
    ):
        await _insert_dataset(
            postgres_with_real_init_schema,
            scope,
            name=name,
            cartridge="salesforce",
            sql=sql,
            mapping={"preserve": name},
            schedule="0 5 * * *",
        )
    before = await _rows(postgres_with_real_init_schema, [scope["id"]])

    with pytest.raises((OSError, ValueError)):
        await _run_manifest(
            omega_console_live_dsn,
            {"salesforce": [good, broken]},
            [scope],
        )

    after = await _rows(postgres_with_real_init_schema, [scope["id"]])
    assert after == before
    assert {row["name"] for row in after} == {
        "salesforce_good_packaged",
        "salesforce_custom_valid",
        "salesforce_parse_failed",
    }
