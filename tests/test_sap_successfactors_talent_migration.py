from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "99p_sap_successfactors_talent_datasets.sql"

TALENT_DATASETS = {
    "sap_successfactors_talent_employee_profile",
    "sap_successfactors_talent_role_profile",
    "sap_successfactors_talent_mobility_history",
    "sap_successfactors_talent_readiness",
    "sap_successfactors_talent_9box",
    "sap_successfactors_talent_signals",
}


def test_talent_incremental_migration_registers_all_gold_datasets():
    sql = MIGRATION.read_text(encoding="utf-8")
    names = set(re.findall(r"\$seed\$(sap_successfactors_talent_[a-z0-9_]+)\$seed\$", sql))

    assert TALENT_DATASETS <= names
    assert "INSERT INTO datasets" in sql
    assert "ON CONFLICT (name) DO UPDATE" in sql
    assert "'99p_sap_successfactors_talent_datasets.sql'" in sql


def test_talent_incremental_migration_is_recommendation_only_and_pii_safe():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "recommendation_only" in sql
    assert "write-back" in sql
    assert "paycomp_value" not in sql
    assert "date_of_birth" not in sql
    assert "national_id" not in sql
