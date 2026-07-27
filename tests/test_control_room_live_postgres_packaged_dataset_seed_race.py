"""Real PostgreSQL proof for concurrent packaged-dataset reconciliation."""

from __future__ import annotations

import asyncio

import pytest

from app.services import seed_packaged_datasets as packaged_seed
from tests.test_control_room_live_postgres_packaged_dataset_seed import (
    _insert_dataset,
    _new_workspaces,
    _rows,
    _run_manifest,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


_FIXTURE_IMPORTS = (omega_console_live_dsn, postgres_with_real_init_schema)


@pytest.mark.asyncio
async def test_two_concurrent_seeders_retry_inside_a_savepoint(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    tmp_path,
    monkeypatch,
) -> None:
    scope = (await _new_workspaces(postgres_with_real_init_schema, 1))[0]
    sql_path = tmp_path / "salesforce_concurrent_seed.sql"
    sql_path.write_text(
        "-- salesforce_concurrent_seed (gold) cartridge: salesforce\n"
        '-- sources: ["salesforce_source"]\n'
        "SELECT 1\n",
        encoding="utf-8",
    )
    packaged = {"salesforce": [sql_path]}
    original_update = packaged_seed._update_dataset_row
    both_updated_zero = asyncio.Event()
    update_count = 0

    async def synchronized_update(*args, **kwargs):
        nonlocal update_count
        updated = await original_update(*args, **kwargs)
        if not updated and update_count < 2:
            update_count += 1
            if update_count == 2:
                both_updated_zero.set()
            await asyncio.wait_for(both_updated_zero.wait(), timeout=5)
        return updated

    monkeypatch.setattr(packaged_seed, "_update_dataset_row", synchronized_update)
    results = await asyncio.gather(
        _run_manifest(omega_console_live_dsn, packaged, [scope]),
        _run_manifest(omega_console_live_dsn, packaged, [scope]),
        return_exceptions=True,
    )

    assert results == [
        {"salesforce": 1},
        {"salesforce": 1},
    ]
    rows = await _rows(postgres_with_real_init_schema, [scope["id"]])
    assert [(row["name"], row["sql_def"]) for row in rows] == [
        ("salesforce_concurrent_seed", sql_path.read_text(encoding="utf-8"))
    ]


@pytest.mark.asyncio
async def test_opposite_manifest_order_cannot_deadlock_concurrent_seeders(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    tmp_path,
    monkeypatch,
) -> None:
    scope = (await _new_workspaces(postgres_with_real_init_schema, 1))[0]
    paths = []
    for suffix in ("a", "b"):
        name = f"salesforce_lock_order_{suffix}"
        sql_path = tmp_path / f"{name}.sql"
        sql_path.write_text(
            f"-- {name} (gold) cartridge: salesforce\n"
            '-- sources: ["salesforce_source"]\n'
            f"SELECT '{suffix}'\n",
            encoding="utf-8",
        )
        paths.append(sql_path)
        await _insert_dataset(
            postgres_with_real_init_schema,
            scope,
            name=name,
            cartridge="salesforce",
            sql=f"SELECT 'old-{suffix}'",
        )

    original_set_scope = packaged_seed._set_seed_scope
    both_ready = asyncio.Event()
    arrivals = 0

    async def synchronized_scope(*args, **kwargs):
        nonlocal arrivals
        await original_set_scope(*args, **kwargs)
        arrivals += 1
        if arrivals == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=5)

    monkeypatch.setattr(packaged_seed, "_set_seed_scope", synchronized_scope)
    forward = {"salesforce": paths}
    reverse = {"salesforce": list(reversed(paths))}
    results = await asyncio.wait_for(
        asyncio.gather(
            _run_manifest(omega_console_live_dsn, forward, [scope]),
            _run_manifest(omega_console_live_dsn, reverse, [scope]),
            return_exceptions=True,
        ),
        timeout=10,
    )

    assert results == [{"salesforce": 2}, {"salesforce": 2}]
