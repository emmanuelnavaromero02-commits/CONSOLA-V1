"""SuccessFactors Gold dependency schedule contract.

The packaged Gold datasets for employee 360/headcount/org/turnover depend on
Foundation Objects and EmpEmploymentTermination. AWS needs those entities to be
scheduled with the same FEMSA scoped Vault connection as the existing seven live
entities.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "99l_sap_successfactors_gold_foundation_schedule.sql"

FOUNDATION_ENTITIES = {
    "FOCompany",
    "FODepartment",
    "FODivision",
    "FOBusinessUnit",
    "FOJobCode",
    "EmpEmploymentTermination",
}


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_gold_dependency_entities_have_odata_select_fields():
    sql = _sql()
    for entity in FOUNDATION_ENTITIES:
        assert f"'sap_successfactors', '{entity}'" in sql
        assert f"('{entity}'," in sql
    for field in (
        "externalCode",
        "name_defaultValue",
        "eventReasonExternalCode",
        "lastModifiedDateTime",
    ):
        assert field in sql


def test_gold_dependency_schedule_is_scoped_to_femsa_connection():
    sql = _sql()
    assert "connection_id     = 'femsa_sf'" in sql
    assert "tenant_id         = femsa_scope.tenant_id" in sql
    assert "workspace_id      = femsa_scope.workspace_id" in sql
    assert "trigger_type      = 'scheduled'" in sql
    assert "sap_successfactors_extract" in sql
    assert "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78" in sql
    assert "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4" in sql


def test_migration_parses_as_postgres_sql():
    sqlglot = pytest.importorskip("sqlglot")
    statements = [stmt for stmt in sqlglot.parse(_sql(), read="postgres") if stmt]
    assert len(statements) >= 3
