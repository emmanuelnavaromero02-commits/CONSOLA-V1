from __future__ import annotations

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
    assert "99zd_datasets_workspace_scoped_catalog" in sql
