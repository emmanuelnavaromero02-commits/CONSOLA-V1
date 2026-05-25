"""Phase 2 Block C3 — SAP SuccessFactors Knowledge Bits.

8 new copilot KBs (prefixed kb_sap_successfactors_) appended to the existing ones,
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
}


def _kbs(path: Path = KBS_YAML) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("knowledge_bits", [])


def _dataset_names(layer: str | None = None) -> set[str]:
    sql = MIGRATION_82.read_text(encoding="utf-8")
    pat = r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$"
    return {n for n, lay in re.findall(pat, sql) if layer is None or lay == layer}


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
    assert len(golds) == 8, f"expected 8 golds in migration 82, got {len(golds)}"
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        refs = re.findall(r"/(?:gold|silver)/sap_successfactors/([a-z0-9_]+)/", kb["sql"])
        assert refs, f"{kb['id']}: reads no sap_successfactors dataset parquet"
        for name in refs:
            assert name in all_datasets, f"{kb['id']}: dataset {name!r} not seeded in migration 82"


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
