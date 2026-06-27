"""Phase 2 Block C3 — SAP SuccessFactors Knowledge Bits.

21 new copilot KBs (prefixed kb_sap_successfactors_) appended to the existing ones,
following the kb_sap_hcm_* / kb_sap_s4hana_* mold (id / name / description / sql,
folded scalar) and the real execution model (DuckDB over the Block-B parquet via
read_parquet, NOT pggold). SF gold dataset names carry the sap_successfactors_
prefix, so the parquet path repeats it: gold/sap_successfactors/sap_successfactors_<x>/.
One KB (workforce_distribution) reads a silver because no gold exposes employee_class.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
KBS_YAML = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "knowledge_bits.yaml"
MIGRATION_82 = REPO_ROOT / "infra" / "init" / "82_sap_successfactors_datasets_seed.sql"
MIGRATION_99P = REPO_ROOT / "infra" / "init" / "99p_sap_successfactors_talent_datasets.sql"
DATASETS_DIR = REPO_ROOT / "cartridges" / "sap_successfactors" / "datasets"
HCM_KBS = REPO_ROOT / "cartridges" / "sap_hcm" / "app" / "config" / "knowledge_bits.yaml"
S4_KBS = REPO_ROOT / "cartridges" / "sap_s4hana" / "app" / "config" / "knowledge_bits.yaml"

REQUIRED_FIELDS = ("id", "name", "description", "sql")
EXISTING_KB_IDS = {
    "kb_employee_360", "kb_headcount_by_department", "kb_talent_pipeline",
    "kb_learning_completion", "kb_performance_distribution", "kb_compensation_analysis",
}
NEW_KB_IDS = {
    "kb_sap_successfactors_headcount_by_department",
    "kb_sap_successfactors_headcount_by_location",
    "kb_sap_successfactors_headcount_by_company",
    "kb_sap_successfactors_recruitment_funnel",
    "kb_sap_successfactors_turnover_recent",
    "kb_sap_successfactors_manager_hierarchy_depth",
    "kb_sap_successfactors_employees_anomalies",
    "kb_sap_successfactors_workforce_distribution",
    "kb_sap_successfactors_talent_employee_profile",
    "kb_sap_successfactors_talent_role_profile",
    "kb_sap_successfactors_talent_mobility_history",
    "kb_sap_successfactors_talent_readiness",
    "kb_sap_successfactors_talent_9box",
    "kb_sap_successfactors_talent_signals",
    "kb_sap_successfactors_talent_cpa_scores",
    "kb_sap_successfactors_talent_9box_operational",
    "kb_sap_successfactors_talent_retention_risk",
    "kb_sap_successfactors_talent_promotion_alignment",
    "kb_sap_successfactors_talent_calibration_sensitivity",
    "kb_sap_successfactors_talent_role_fit_assignments",
    "kb_sap_successfactors_talent_action_candidates",
}
EXPECTED_GOLD_DATASETS = 33


def _kbs(path: Path = KBS_YAML) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("knowledge_bits", [])


def _dataset_names(layer: str | None = None) -> set[str]:
    names: set[str] = set()
    header = re.compile(r"^--\s+([a-z0-9_]+)\s+\((silver|gold)\)\s+cartridge:\s+sap_successfactors")
    for path in DATASETS_DIR.glob("*.sql"):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        match = header.match(first)
        if match and (layer is None or match.group(2) == layer):
            names.add(match.group(1))
    return names


def test_yaml_loads_with_required_fields():
    kbs = _kbs()
    assert kbs, "knowledge_bits.yaml is empty"
    for kb in kbs:
        for field in REQUIRED_FIELDS:
            assert kb.get(field), f"{kb.get('id')}: missing required field {field}"


def test_existing_kbs_intact():
    ids = {kb["id"] for kb in _kbs()}
    missing = EXISTING_KB_IDS - ids
    assert not missing, f"existing KBs removed: {missing}"


def test_new_kbs_present_and_prefixed():
    ids = {kb["id"] for kb in _kbs()}
    assert NEW_KB_IDS <= ids, f"new KBs missing: {NEW_KB_IDS - ids}"
    for kb_id in NEW_KB_IDS:
        assert kb_id.startswith("kb_sap_successfactors_"), f"{kb_id} not prefixed"


def test_kb_ids_unique():
    ids = [kb["id"] for kb in _kbs()]
    assert len(ids) == len(set(ids)), "duplicate KB ids"


def test_new_kb_sql_parses():
    sqlglot = pytest.importorskip("sqlglot")
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        stmts = [s for s in sqlglot.parse(kb["sql"], read="duckdb") if s]
        assert stmts, f"{kb['id']}: SQL does not parse"


def test_new_kbs_read_existing_datasets():
    all_datasets = _dataset_names()
    golds = _dataset_names("gold")
    assert len(golds) == EXPECTED_GOLD_DATASETS, (
        f"expected {EXPECTED_GOLD_DATASETS} golds in SuccessFactors migrations, got {len(golds)}"
    )
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        refs = re.findall(r"/(?:gold|silver)/sap_successfactors/([a-z0-9_]+)/", kb["sql"])
        assert refs, f"{kb['id']}: reads no sap_successfactors dataset parquet"
        for name in refs:
            assert name in all_datasets, f"{kb['id']}: dataset {name!r} not packaged for SuccessFactors"


def test_new_kb_sql_uses_parquet_not_pggold():
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        sql = kb["sql"].lower()
        assert "pggold" not in sql, f"{kb['id']}: references pggold"
        assert "read_parquet(" in sql, f"{kb['id']}: does not use read_parquet"


def test_no_collision_with_other_cartridge_kbs():
    sf_ids = {kb["id"] for kb in _kbs()}
    assert not (sf_ids & _dataset_names()), "a KB id collides with a dataset name"
    hcm_ids = {kb["id"] for kb in _kbs(HCM_KBS)}
    s4_ids = {kb["id"] for kb in _kbs(S4_KBS)}
    assert not (NEW_KB_IDS & hcm_ids), "new SF KB id collides with an HCM KB id"
    assert not (NEW_KB_IDS & s4_ids), "new SF KB id collides with an S4 KB id"
