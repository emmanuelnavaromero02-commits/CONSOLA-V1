"""Targeted Control Room refresh for guarded SuccessFactors headcounts."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.system.lifespan import _default_startup_seeds
from app.services import successfactors_headcount_seed_refresh as refresh


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

    def transaction(self):
        return _Context(self)


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return _Context(self.connection)


def _packaged_files() -> dict[str, list[Path]]:
    names = [*refresh.HEADCOUNT_DATASET_NAMES, "sap_successfactors_employee_360"]
    return {"sap_successfactors": [Path(f"/registry/{name}.sql") for name in names]}


def test_targeted_refresh_is_registered_in_the_existing_startup_seed_flow():
    components = [component for component, _seed in _default_startup_seeds()]
    assert "refresh_successfactors_headcount_definitions" in components


def test_selector_fails_closed_if_any_guarded_definition_is_missing():
    packaged = _packaged_files()
    packaged["sap_successfactors"] = packaged["sap_successfactors"][:-2]
    with pytest.raises(RuntimeError, match="missing packaged"):
        refresh._selected_files(packaged)


@pytest.mark.asyncio
async def test_refresh_is_idempotent_and_never_prunes_or_touches_other_datasets(
    monkeypatch,
):
    upserts: list[dict[str, object]] = []

    async def yes(*_args):
        return True

    async def set_scope(*_args):
        return None

    async def upsert(_conn, **kwargs):
        upserts.append(kwargs)

    monkeypatch.setattr(refresh, "_dataset_files", _packaged_files)
    monkeypatch.setattr(refresh, "_datasets_has_column", yes)
    monkeypatch.setattr(refresh, "_datasets_workspace_name_conflict_available", yes)
    monkeypatch.setattr(refresh, "_parse_dataset", lambda path: {"name": path.stem})
    monkeypatch.setattr(refresh, "_set_seed_scope", set_scope)
    monkeypatch.setattr(refresh, "_upsert_dataset_row", upsert)

    pool = _Pool()
    await refresh.refresh_successfactors_headcount_definitions(pool)
    await refresh.refresh_successfactors_headcount_definitions(pool)

    assert len(upserts) == 6
    assert {call["dataset"]["name"] for call in upserts} == (
        refresh.HEADCOUNT_DATASET_NAMES
    )
    assert all(call["workspace_id"] == "workspace-a" for call in upserts)
