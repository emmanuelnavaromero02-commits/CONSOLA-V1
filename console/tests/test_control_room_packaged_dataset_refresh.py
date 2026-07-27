"""The general packaged-dataset seed is the single startup refresh path."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.system.lifespan import _default_startup_seeds
from app.services import seed_packaged_datasets as packaged_seed


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    async def fetch(self, sql: str):
        assert "FROM workspaces" in sql
        return [{"id": "workspace-a", "tenant_id": "tenant-a"}]

    async def execute(self, sql: str, *_args):
        assert "DELETE FROM datasets" in sql
        return "DELETE 0"

    def transaction(self):
        return _Context(self)


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _Context(self.connection)


def _dataset(path: Path, name: str, sql: str) -> Path:
    path.write_text(
        f"-- {name} (gold) cartridge: sap_successfactors\n"
        '-- sources: ["sap_successfactors_employee_360"]\n'
        f"{sql}\n",
        encoding="utf-8",
    )
    return path


def test_startup_registers_the_general_dataset_seed_exactly_once():
    components = [component for component, _seed in _default_startup_seeds()]

    assert components.count("seed_packaged_datasets") == 1
    assert "refresh_successfactors_headcount_definitions" not in components


@pytest.mark.asyncio
async def test_general_seed_refreshes_obsolete_definition_idempotently(
    monkeypatch,
    tmp_path,
):
    headcount_name = "sap_successfactors_headcount_by_company"
    headcount_sql = "SELECT company_name, COUNT(*) AS headcount FROM employee_360"
    headcount = _dataset(
        tmp_path / f"{headcount_name}.sql", headcount_name, headcount_sql
    )
    employee = _dataset(
        tmp_path / "sap_successfactors_employee_360.sql",
        "sap_successfactors_employee_360",
        "SELECT * FROM employee_source",
    )
    installed = {headcount_name: "SELECT 'obsolete'"}
    writes: list[str] = []
    scopes: list[tuple[object, object]] = []

    async def yes(*_args):
        return True

    async def set_scope(_conn, tenant_id, workspace_id):
        scopes.append((tenant_id, workspace_id))

    async def upsert(_conn, *, dataset, **_kwargs):
        writes.append(dataset["name"])
        installed[dataset["name"]] = dataset["sql"]

    monkeypatch.setattr(
        packaged_seed,
        "_dataset_files",
        lambda: {"sap_successfactors": [headcount, employee]},
    )
    monkeypatch.setattr(packaged_seed, "_datasets_has_column", yes)
    monkeypatch.setattr(
        packaged_seed,
        "_datasets_workspace_name_conflict_available",
        yes,
    )
    monkeypatch.setattr(packaged_seed, "_set_seed_scope", set_scope)
    monkeypatch.setattr(packaged_seed, "_upsert_dataset_row", upsert)

    pool = _Pool()
    await packaged_seed.seed_packaged_datasets(pool)
    first_result = dict(installed)
    await packaged_seed.seed_packaged_datasets(pool)

    assert first_result[headcount_name].endswith(f"{headcount_sql}\n")
    assert installed == first_result
    assert writes == [employee.stem, headcount_name, employee.stem, headcount_name]
    assert scopes == [("tenant-a", "workspace-a")] * 2
