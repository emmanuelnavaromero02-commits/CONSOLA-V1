from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "99p_sap_successfactors_talent_datasets.sql"
OPERATIONAL_MIGRATION = REPO_ROOT / "infra" / "init" / "99zf_sap_successfactors_talent_operational_features.sql"
OPERATIONAL_CONTRACT_V2 = REPO_ROOT / "infra" / "init" / "99zj_sap_successfactors_talent_operational_contract_v2.sql"
AGENTOPS_CONTRACT_V2 = REPO_ROOT / "infra" / "init" / "99zi_sap_successfactors_talent_agentops_feature_pack.sql"
AGENTOPS_READY_REPAIR = REPO_ROOT / "infra" / "init" / "99zk_sap_successfactors_talent_agentops_ready_only.sql"
DATASET_CONTRACT_REPAIR = REPO_ROOT / "infra" / "init" / "99zl_sap_successfactors_talent_dataset_contract_patch.sql"

TALENT_DATASETS = {
    "sap_successfactors_talent_employee_profile",
    "sap_successfactors_talent_role_profile",
    "sap_successfactors_talent_mobility_history",
    "sap_successfactors_talent_cpa_scores",
    "sap_successfactors_talent_benchmark_internal",
    "sap_successfactors_talent_readiness",
    "sap_successfactors_talent_9box",
    "sap_successfactors_talent_9box_operational",
    "sap_successfactors_talent_retention_risk",
    "sap_successfactors_talent_promotion_alignment",
    "sap_successfactors_talent_calibration_sensitivity",
    "sap_successfactors_talent_role_fit_assignments",
    "sap_successfactors_talent_action_candidates",
    "sap_successfactors_talent_signals",
    "sap_successfactors_talent_operational_features",
    "sap_successfactors_talent_simulation_inputs",
}


def test_talent_incremental_migration_registers_all_gold_datasets():
    sql = (
        MIGRATION.read_text(encoding="utf-8")
        + OPERATIONAL_MIGRATION.read_text(encoding="utf-8")
        + OPERATIONAL_CONTRACT_V2.read_text(encoding="utf-8")
    )

    for dataset in TALENT_DATASETS:
        assert dataset in sql
    assert "INSERT INTO datasets" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert "ON CONFLICT (name)" not in sql
    assert "'99p_sap_successfactors_talent_datasets.sql'" in sql
    assert "'99zf_sap_successfactors_talent_operational_features.sql'" in sql
    assert "'99zj_sap_successfactors_talent_operational_contract_v2.sql'" in sql


def test_talent_incremental_migration_is_recommendation_only_and_pii_safe():
    sql = (
        MIGRATION.read_text(encoding="utf-8")
        + OPERATIONAL_MIGRATION.read_text(encoding="utf-8")
        + OPERATIONAL_CONTRACT_V2.read_text(encoding="utf-8")
    ).lower()

    assert "recommendation_only" in sql
    assert "write-back" in sql
    assert "paycomp_value" not in sql
    assert "date_of_birth" not in sql
    assert "national_id" not in sql


def test_talent_operational_feature_pack_is_aggregate_only():
    sql = OPERATIONAL_CONTRACT_V2.read_text(encoding="utf-8").lower()

    assert "sap_successfactors_talent_operational_features" in sql
    assert "sap_successfactors_talent_simulation_inputs" in sql
    assert "sap_successfactors_talent_benchmark_internal" in sql
    assert "workspace_id" in sql
    assert "materialized_at" in sql
    assert "employee_count" in sql
    assert "open_signals_by_severity" in sql
    assert "nine_box_blocked_count" in sql
    assert "confidence" in sql
    assert "readiness_status" in sql
    assert "skill_coverage_pct" in sql
    assert "full_name" not in sql
    assert "email" not in sql
    assert "salary" not in sql


def test_talent_agentops_monitor_uses_feature_pack_inputs():
    sql = (
        AGENTOPS_CONTRACT_V2.read_text(encoding="utf-8")
        + OPERATIONAL_CONTRACT_V2.read_text(encoding="utf-8")
        + AGENTOPS_READY_REPAIR.read_text(encoding="utf-8")
        + DATASET_CONTRACT_REPAIR.read_text(encoding="utf-8")
    )

    assert "sap_successfactors_talent_simulation_inputs" in sql
    assert "input_variables_json" in sql
    assert "missing_simulation_inputs" in sql
    assert '"ready_statuses": ["ready"]' in sql
    assert "WHEN readiness_status IN ('ready', 'benchmark_internal') THEN 3" in sql
    assert "WHEN readiness_status = 'partial' THEN 'missing_simulation_inputs'" in sql
    assert '"baseline_value": {"type": "fixed", "value": 100}' not in sql
