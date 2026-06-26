from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_datasets_catalog_is_workspace_scoped() -> None:
    sql = (
        ROOT / "infra" / "init" / "99zd_datasets_workspace_scoped_catalog.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE public.datasets DROP CONSTRAINT datasets_pkey" in sql
    assert "datasets_workspace_name_key" in sql
    assert "UNIQUE (workspace_id, name)" in sql
    assert "ADD COLUMN IF NOT EXISTS tenant_id" in sql
    assert "ADD COLUMN IF NOT EXISTS scope_status" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "filename TEXT NOT NULL UNIQUE" in sql
    assert "INSERT INTO schema_migrations(filename, applied_at)" in sql
    assert "99zd_datasets_workspace_scoped_catalog.sql" in sql


def test_replicon_mejoras_seed_uses_workspace_scoped_dataset_conflicts() -> None:
    sql = (
        ROOT / "infra" / "init" / "65_replicon_mejoras_seed_refresh.sql"
    ).read_text(encoding="utf-8")
    before_apps = sql.split("INSERT INTO analytic_apps", 1)[0]

    dataset_inserts = re.findall(r"INSERT INTO datasets\b", before_apps)
    workspace_conflicts = re.findall(
        r"ON CONFLICT \(workspace_id, name\) DO NOTHING", before_apps
    )

    assert dataset_inserts
    assert len(workspace_conflicts) == len(dataset_inserts)
    assert "ON CONFLICT (name) DO NOTHING" not in before_apps
