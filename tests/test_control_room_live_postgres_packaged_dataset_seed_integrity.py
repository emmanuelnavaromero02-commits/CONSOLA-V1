from __future__ import annotations

from pathlib import Path

import pytest

from app.services import seed_packaged_datasets as packaged_seed
from tests.test_control_room_live_postgres_packaged_dataset_seed import (
    REPO_ROOT,
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
async def test_catalog_content_drift_aborts_before_degrading_existing_rows(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    canonical_registry = REPO_ROOT / "cartridges"
    temporary_registry = tmp_path / "registry"
    canonical_paths = sorted(canonical_registry.glob("*/datasets/*.sql"))
    for source in canonical_paths:
        target = temporary_registry / source.relative_to(canonical_registry)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source)
    changed_path = (
        temporary_registry
        / "sap_successfactors/datasets/sap_successfactors_headcount_by_company.sql"
    )
    changed_path.unlink()
    changed_path.write_text(
        "-- sap_successfactors_headcount_by_company (gold) "
        "cartridge: sap_successfactors\n",
        encoding="utf-8",
    )

    scope = (await _new_workspaces(postgres_with_real_init_schema, 1))[0]
    await _insert_dataset(
        postgres_with_real_init_schema,
        scope,
        name="sap_successfactors_headcount_by_company",
        cartridge="sap_successfactors",
        sql="SELECT 100 AS healthy",
        mapping={"healthy": "preserved"},
        schedule="30 2 * * *",
    )
    before = await _rows(postgres_with_real_init_schema, [scope["id"]])
    monkeypatch.setattr(packaged_seed, "_REGISTRY", temporary_registry)

    error: ValueError | None = None
    try:
        discovered = packaged_seed._dataset_files()
        await _run_manifest(omega_console_live_dsn, discovered, [scope])
    except ValueError as exc:
        error = exc

    after = await _rows(postgres_with_real_init_schema, [scope["id"]])
    assert after == before
    assert error is not None
    assert "integrity" in str(error)
