from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
KBS_YAML = REPO_ROOT / "cartridges" / "sap_s4hana" / "app" / "config" / "knowledge_bits.yaml"
MIGRATION_81 = REPO_ROOT / "infra" / "init" / "81_sap_s4hana_datasets_seed.sql"
HCM_KBS_YAML = REPO_ROOT / "cartridges" / "sap_hcm" / "app" / "config" / "knowledge_bits.yaml"

REQUIRED_FIELDS = ("id", "name", "description", "sql")
EXISTING_KB_IDS = {
    "kb_open_sales_orders", "kb_overdue_invoices",
    "kb_purchase_spend_by_supplier", "kb_gl_balance_by_account",
}
NEW_KB_IDS = {
    "kb_sap_s4hana_revenue_top_customers",
    "kb_sap_s4hana_revenue_by_month",
    "kb_sap_s4hana_open_sales_backlog",
    "kb_sap_s4hana_overdue_invoices",
    "kb_sap_s4hana_top_suppliers_spend",
    "kb_sap_s4hana_gl_balance_summary",
    "kb_sap_s4hana_inventory_movements_recent",
    "kb_sap_s4hana_business_partner_anomalies",
}


def _kbs(path: Path = KBS_YAML) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("knowledge_bits", [])


def _gold_dataset_names() -> set[str]:
    sql = MIGRATION_81.read_text(encoding="utf-8")
    pat = r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$"
    return {n for n, layer in re.findall(pat, sql) if layer == "gold"}


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
        assert kb_id.startswith("kb_sap_s4hana_"), f"{kb_id} not prefixed kb_sap_s4hana_"


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
    assert len(golds) == 8, f"expected 8 golds in migration 81, got {len(golds)}"
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        refs = re.findall(r"gold/sap_s4hana/(\w+)/", kb["sql"])
        assert refs, f"{kb['id']}: reads no gold/sap_s4hana/<name> parquet"
        for name in refs:
            assert name in golds, f"{kb['id']}: gold {name!r} not seeded in migration 81"


def test_new_kb_sql_uses_parquet_not_pggold():
    for kb in _kbs():
        if kb["id"] not in NEW_KB_IDS:
            continue
        sql = kb["sql"].lower()
        assert "pggold" not in sql, f"{kb['id']}: references pggold"
        assert "read_parquet(" in sql, f"{kb['id']}: does not use read_parquet"


def test_no_collision_with_datasets_or_hcm_kbs():
    s4_ids = {kb["id"] for kb in _kbs()}
    golds = _gold_dataset_names()
    assert not (s4_ids & golds), "a KB id collides with a dataset name"
    hcm_ids = {kb["id"] for kb in _kbs(HCM_KBS_YAML)}
    assert not (NEW_KB_IDS & hcm_ids), "new S4 KB id collides with an HCM KB id"
