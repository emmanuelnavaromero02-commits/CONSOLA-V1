"""SuccessFactors live Silver schedule and migration contract.

The AWS FEMSA flow uses a scoped Vault connection (`femsa_sf`) and Airflow runs
without an interactive user. This migration must therefore carry tenant,
workspace, and connection id into `entity_config`, while the Silver catalog is
updated for existing installs that already applied migration 82.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "99k_sap_successfactors_silver_schedule.sql"

LIVE_ENTITIES = {
    "PerPerson",
    "PerPersonal",
    "PerEmail",
    "EmpEmployment",
    "EmpJob",
    "PaymentInformationDetailV3",
    "FOLocation",
}


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_seeds_live_silver_catalog_schema_agnostically():
    sql = _sql()
    assert "ON CONFLICT DO NOTHING" in sql
    assert "ON CONFLICT (name)" not in sql
    for dataset in (
        "sap_successfactors_perperson_latest",
        "sap_successfactors_perpersonal_latest",
        "sap_successfactors_peremail_latest",
        "sap_successfactors_empemployment_latest",
        "sap_successfactors_empjob_latest",
        "sap_successfactors_paymentinformationdetailv3_latest",
        "sap_successfactors_folocation_latest",
    ):
        assert dataset in sql


def test_migration_adds_scheduler_scope_columns_before_use():
    sql = _sql()
    assert "ALTER TABLE entity_config" in sql
    assert "ADD COLUMN IF NOT EXISTS tenant_id UUID" in sql
    assert "ADD COLUMN IF NOT EXISTS workspace_id UUID" in sql
    assert sql.index("ADD COLUMN IF NOT EXISTS tenant_id UUID") < sql.index("UPDATE entity_config ec")
    assert sql.index("ADD COLUMN IF NOT EXISTS workspace_id UUID") < sql.index("UPDATE entity_config ec")


def test_femsa_schedule_is_scoped_and_uses_vault_connection():
    sql = _sql()
    assert "connection_id     = 'femsa_sf'" in sql
    assert "tenant_id         = femsa_scope.tenant_id" in sql
    assert "workspace_id      = femsa_scope.workspace_id" in sql
    assert "trigger_type      = 'scheduled'" in sql
    assert "sap_successfactors_extract" in sql
    for entity in LIVE_ENTITIES:
        assert f"('{entity}'," in sql, f"{entity} missing from daily schedule"


def test_migration_is_guarded_to_femsa_scope_existing():
    sql = _sql()
    assert "WHERE EXISTS (SELECT 1 FROM tenants WHERE id =" in sql
    assert "AND EXISTS (SELECT 1 FROM workspaces WHERE id =" in sql
    assert "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78" in sql
    assert "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4" in sql


def test_migration_parses_as_postgres_sql():
    sqlglot = pytest.importorskip("sqlglot")
    statements = [stmt for stmt in sqlglot.parse(_sql(), read="postgres") if stmt]
    assert len(statements) >= 5
