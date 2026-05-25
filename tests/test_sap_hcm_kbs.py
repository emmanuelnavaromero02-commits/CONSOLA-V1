"""Phase 2 Block C — SAP HCM Knowledge Bits.

7 new copilot KBs (prefixed kb_sap_hcm_) appended to the existing 5, following
the platform's real KB schema (id / name / description / sql — the fields
kb_config and catalog_service actually read) and the real execution model (DuckDB
over parquet via read_parquet, NOT pggold). The new KBs read the Block-B gold
parquet at gold/sap_hcm/<name>/.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
KBS_YAML = REPO_ROOT / "cartridges" / "sap_hcm" / "app" / "config" / "knowledge_bits.yaml"
MIGRATION_80 = REPO_ROOT / "infra" / "init" / "80_sap_hcm_datasets_seed.sql"

REQUIRED_FIELDS = ("id", "name", "description", "sql")
EXISTING_KB_IDS = {
    "kb_headcount_snapshot", "kb_employee_actions_30d", "kb_absence_analysis",
    "kb_org_hierarchy", "kb_contract_type_distribution",
}
NEW_KB_IDS = {
    "kb_sap_hcm_headcount_active_by_department",
    "kb_sap_hcm_headcount_by_costcenter",
    "kb_sap_hcm_absence_top_employees",
    "kb_sap_hcm_absence_trend_monthly",
    "kb_sap_hcm_employees_anomalies_active",
    "kb_sap_hcm_manager_span_of_control",
    "kb_sap_hcm_workforce_composition_by_position_type",
}


def _kbs() -> list[dict]:
    data = yaml.safe_load(KBS_YAML.read_text(encoding="utf-8")) or {}
    return data.get("knowledge_bits", [])


def _gold_dataset_names() -> set[str]:
    sql = MIGRATION_80.read_text(encoding="utf-8")
    return {name for name, layer in re.findall(r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$", sql) if layer == "gold"}


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
        assert kb_id.startswith("kb_sap_hcm_"), f"{kb_id} not prefixed kb_sap_hcm_"


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


def test_new_kbs_read_existing_gold_datasets():
    golds = _gold_dataset_names()
    assert len(golds) == 8
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        refs = re.findall(r"gold/sap_hcm/(\w+)/", kb["sql"])
        assert refs, f"{kb['id']}: reads no gold/sap_hcm/<name> parquet"
        for name in refs:
            assert name in golds, f"{kb['id']}: gold {name!r} not seeded in migration 80"


def test_new_kb_sql_executes_in_duckdb_not_pggold():
    # Real execution is DuckDB over parquet; pggold is not attached in run_kb_sql.
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        assert "pggold" not in kb["sql"].lower(), f"{kb['id']}: references pggold (not attached in KB DuckDB)"
        assert "read_parquet(" in kb["sql"], f"{kb['id']}: does not use read_parquet"
