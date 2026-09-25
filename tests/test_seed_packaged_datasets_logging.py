from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def seed_module():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = (
        "/cartridges/",
        "/console",
        "/refinement",
        "/vault",
        "/workspace",
        "/mcp-infra",
    )
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    return importlib.import_module("app.services.seed_packaged_datasets")


def test_invalid_sources_aborts_manifest(seed_module, tmp_path):
    sql_file = tmp_path / "replicon_demo.sql"
    sql_file.write_text(
        "-- replicon_demo (gold) cartridge: replicon\n"
        "-- sources: [not-valid-json\n"
        "-- description: demo\n"
        "SELECT 1 AS ok\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid packaged dataset sources"):
        seed_module._parse_dataset(sql_file)


def test_manifest_rejects_duplicate_names_across_cartridges(seed_module, tmp_path):
    paths = {}
    for cartridge in ("alpha", "beta"):
        path = tmp_path / f"{cartridge}.sql"
        path.write_text(
            f"-- shared_name (gold) cartridge: {cartridge}\nSELECT 1\n",
            encoding="utf-8",
        )
        paths[cartridge] = [path]

    with pytest.raises(ValueError, match="duplicate packaged dataset 'shared_name'"):
        seed_module._load_packaged_manifest(paths)


def test_discovery_rejects_truncated_versioned_catalog(
    seed_module, tmp_path, monkeypatch
):
    datasets = tmp_path / "salesforce" / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "only_one.sql").write_text(
        "-- only_one (gold) cartridge: salesforce\nSELECT 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(seed_module, "_REGISTRY", tmp_path)

    with pytest.raises(ValueError, match="catalog integrity check failed"):
        seed_module._dataset_files()


@pytest.mark.asyncio
async def test_startup_empty_registry_fails_closed(seed_module, monkeypatch):
    monkeypatch.setattr(seed_module, "_dataset_files", lambda: {})

    with pytest.raises(ValueError, match="manifest is empty"):
        await seed_module.seed_packaged_datasets(object())


@pytest.mark.asyncio
async def test_workspace_missing_cartridge_fails_closed(seed_module, monkeypatch):
    monkeypatch.setattr(seed_module, "_dataset_files", lambda: {})

    with pytest.raises(ValueError, match="manifest is empty"):
        await seed_module.seed_packaged_datasets_for_workspace(
            object(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            cartridge_id="sap_successfactors",
        )


def test_seed_packaged_datasets_sets_rls_scope_before_writes(seed_module):
    row_module = importlib.import_module("app.services.seed_packaged_dataset_rows")
    seed_source = inspect.getsource(seed_module.seed_packaged_datasets)
    module_source = inspect.getsource(seed_module) + inspect.getsource(row_module)

    assert "SELECT id, tenant_id" in seed_source
    assert "conn.transaction()" in seed_source
    assert "set_config('app.tenant_id'" in module_source
    assert "set_config('app.workspace_id'" in module_source
    assert "tenant_id = $7" in module_source


def test_seed_packaged_datasets_seeds_every_workspace(seed_module):
    row_module = importlib.import_module("app.services.seed_packaged_dataset_rows")
    source = inspect.getsource(seed_module) + inspect.getsource(row_module)
    seed_source = inspect.getsource(seed_module.seed_packaged_datasets)
    writer_source = inspect.getsource(seed_module._seed_packaged_dataset_rows)
    update_source = inspect.getsource(row_module.update_dataset_row)

    assert "def datasets_workspace_name_conflict_available" in source
    assert "_require_workspace_scoped_dataset_schema(conn)" in seed_source
    assert "target_workspaces=list(workspaces)" in seed_source
    assert "LOCK TABLE public.datasets IN ROW SHARE MODE" in source
    assert "datasets_workspace_name_key is required" in source
    assert "for workspace in workspaces:" in writer_source
    assert "def _upsert_dataset_row" in source
    assert "asyncpg.UniqueViolationError" in source
    assert "ON CONFLICT" not in writer_source
    assert "_load_packaged_manifest(packaged)" in writer_source
    assert "DELETE FROM datasets" not in writer_source
    assert update_source.count("WHERE name = $1") == 2
    assert update_source.count("AND workspace_id") == 2


def test_seed_insert_uses_valid_empty_json_for_both_schema_shapes(seed_module):
    row_module = importlib.import_module("app.services.seed_packaged_dataset_rows")
    source = inspect.getsource(row_module.insert_dataset_row)

    assert source.count("'{}'::jsonb") == 2
    assert "'{{}}'::jsonb" not in source


def test_seed_packaged_datasets_for_workspace_is_scoped(seed_module):
    source = inspect.getsource(seed_module.seed_packaged_datasets_for_workspace)

    assert "tenant_id and workspace_id are required" in source
    assert "_require_workspace_scoped_dataset_schema(conn)" in source
    assert '"cartridge_id": cartridge_id' in source
    assert 'target_workspaces=[{"id": workspace_id, "tenant_id": tenant_id}]' in source
    assert '"status": "success"' in source
