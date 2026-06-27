"""P11 — packaged-dataset seeding must surface bad data, not swallow it.

``_parse_dataset`` parses a ``-- sources: [...]`` header line. When the JSON is
malformed it must keep parsing (best-effort seed) but log a WARNING with the
offending path and exception, instead of silently dropping the error.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def seed_module():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    return importlib.import_module("app.services.seed_packaged_datasets")


def test_invalid_sources_logs_warning_and_continues(seed_module, tmp_path, caplog):
    sql_file = tmp_path / "replicon_demo.sql"
    sql_file.write_text(
        "-- sources: [not-valid-json\n-- description: demo\nSELECT 1 AS ok\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        result = seed_module._parse_dataset(sql_file)

    # Best-effort: parsing still returns a dataset with empty sources.
    assert result["sources"] == []
    assert result["sql"].strip().endswith("SELECT 1 AS ok")
    # And the failure is surfaced, not swallowed.
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "invalid sources" in messages
    assert "replicon_demo.sql" in messages


def test_seed_packaged_datasets_sets_rls_scope_before_writes(seed_module):
    seed_source = inspect.getsource(seed_module.seed_packaged_datasets)
    writer_source = inspect.getsource(seed_module._seed_packaged_dataset_rows)
    module_source = inspect.getsource(seed_module)

    assert "SELECT id, tenant_id" in seed_source
    assert "conn.transaction()" in seed_source
    assert "set_config('app.tenant_id'" in module_source
    assert "set_config('app.workspace_id'" in module_source
    assert "tenant_id = $7" in module_source


def test_seed_packaged_datasets_seeds_every_workspace(seed_module):
    source = inspect.getsource(seed_module)
    seed_source = inspect.getsource(seed_module.seed_packaged_datasets)
    writer_source = inspect.getsource(seed_module._seed_packaged_dataset_rows)

    assert "def _datasets_workspace_name_conflict_available" in source
    assert "target_workspaces = workspaces if scoped_conflict else workspaces[:1]" in seed_source
    assert "for workspace in target_workspaces:" in writer_source
    assert "def _upsert_dataset_row" in source
    assert "asyncpg.UniqueViolationError" in source
    assert "ON CONFLICT" not in writer_source
    assert "WHERE cartridge = $1" in writer_source
    assert "AND workspace_id = $2::uuid" in writer_source


def test_seed_packaged_datasets_for_workspace_is_scoped(seed_module):
    source = inspect.getsource(seed_module.seed_packaged_datasets_for_workspace)

    assert "tenant_id and workspace_id are required" in source
    assert '"cartridge_id": cartridge_id' in source
    assert 'target_workspaces=[{"id": workspace_id, "tenant_id": tenant_id}]' in source
    assert '"status": "success"' in source
