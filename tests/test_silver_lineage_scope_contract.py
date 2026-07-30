from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra/init/99zzd_silver_lineage_scope.sql"


def test_silver_lineage_migration_quarantines_legacy_and_forces_scope():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "omega_quarantine.silver_lineage_legacy" in sql
    assert "ALTER COLUMN tenant_id SET NOT NULL" in sql
    assert "ALTER COLUMN workspace_id SET NOT NULL" in sql
    assert "FOREIGN KEY (tenant_id, workspace_id)" in sql
    assert "REFERENCES workspaces(tenant_id, id)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "ALTER ROLE omega_airflow_dag NOBYPASSRLS" in sql
    assert "DROP INDEX IF EXISTS data_catalog_dataset_column_uq" in sql
    assert "data_catalog_scoped_dataset_column_key" in sql
    assert "USING (true)" not in sql.lower()
    assert "storage_uri like" not in sql.lower()
    assert "regexp" not in sql.lower()
