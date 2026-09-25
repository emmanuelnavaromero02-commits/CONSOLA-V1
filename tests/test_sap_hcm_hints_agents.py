from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HINTS = REPO_ROOT / "cartridges" / "sap_hcm" / "hints" / "assistant.md"
AGENTS_MIGRATION = REPO_ROOT / "infra" / "init" / "84_sap_hcm_agents_seed.sql"
CARTRIDGE_SEED = REPO_ROOT / "cartridges" / "sap_hcm" / "config" / "seed.sql"
KBS_YAML = REPO_ROOT / "cartridges" / "sap_hcm" / "app" / "config" / "knowledge_bits.yaml"
MIGRATION_80 = REPO_ROOT / "infra" / "init" / "80_sap_hcm_datasets_seed.sql"
APPS_DIR = REPO_ROOT / "cartridges" / "sap_hcm" / "apps"

MAX_HINTS_CHARS = 8000
AGENT_SLUGS = ["sap_hcm_auditor_org_chart", "sap_hcm_analista_workforce"]
REQUIRED_SECTIONS = [
    "Identidad del cartucho",
    "Modelo de datos",
    "Convenciones",
    "Reglas operativas",
    "Apps publicadas",
    "Limitaciones honestas",
]
FORBIDDEN_SQL = re.compile(
    r"\b(drop|truncate|delete|copy|create\s+extension|create\s+function|"
    r"create\s+procedure|do\s+\$|grant|revoke|alter\s+system|attach|dblink|"
    r"foreign\s+server|foreign\s+table)\b|\\",
    re.IGNORECASE,
)


def _hints() -> str:
    return HINTS.read_text(encoding="utf-8")


def _kb_ids() -> set[str]:
    data = yaml.safe_load(KBS_YAML.read_text(encoding="utf-8")) or {}
    return {kb["id"] for kb in data.get("knowledge_bits", [])}


def _gold_dataset_names() -> set[str]:
    sql = MIGRATION_80.read_text(encoding="utf-8")
    pat = r"\$seed\$([a-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$"
    return {n for n, layer in re.findall(pat, sql) if layer == "gold"}


def _triggers_by_agent(sql: str) -> list[list[str]]:
    return [json.loads(m) for m in re.findall(r'"triggers"\s*:\s*(\[[^\]]*\])', sql)]


def test_hints_exist_and_markdown():
    assert HINTS.is_file(), "hints/assistant.md missing"
    text = _hints()
    assert text.lstrip().startswith("#"), "hints does not start with a markdown header"
    assert text.count("##") >= 6, "expected at least 6 markdown sections"


def test_hints_has_six_sections():
    text = _hints()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"hints missing section: {section!r}"


def test_hints_under_injection_cap():
    text = _hints()
    assert len(text) <= MAX_HINTS_CHARS, f"hints {len(text)} chars exceed cap {MAX_HINTS_CHARS}"
    for token in ("</hints_cartucho>", "<system>", "<|im_start|>"):
        assert token not in text, f"hints contains injection token {token!r}"


def test_hints_references_existing_kbs():
    referenced = set(re.findall(r"kb_sap_hcm_[a-z0-9_]+", _hints()))
    assert referenced, "hints references no kb_sap_hcm_* KB"
    real = _kb_ids()
    for kb in referenced:
        assert kb in real, f"hints references unknown KB {kb!r}"


def test_hints_references_existing_golds():
    referenced = set(re.findall(r"gold_([a-z0-9_]+)", _hints()))
    assert referenced, "hints references no gold_* dataset"
    golds = _gold_dataset_names()
    for name in referenced:
        assert name in golds, f"hints references unknown gold dataset gold_{name}"


def test_hints_references_existing_apps():
    referenced = set(re.findall(r"sap_hcm_[a-z_]+_dashboard", _hints()))
    assert referenced, "hints references no app"
    for app in referenced:
        assert (APPS_DIR / f"{app}.html").is_file(), f"hints references missing app {app}"


def test_agents_registered_in_both_places():
    for path in (AGENTS_MIGRATION, CARTRIDGE_SEED):
        sql = path.read_text(encoding="utf-8")
        assert "INSERT INTO agents" in sql, f"{path.name}: no agents insert"
        for slug in AGENT_SLUGS:
            assert f"'{slug}'" in sql, f"{path.name}: missing agent {slug}"


def test_agents_insert_has_required_columns():
    sql = AGENTS_MIGRATION.read_text(encoding="utf-8")
    cols = re.search(r"INSERT INTO agents\s*\(([^)]+)\)", sql)
    assert cols, "no agents insert column list"
    names = {c.strip() for c in cols.group(1).replace("\n", " ").split(",")}
    required = {"cartridge_id", "slug", "name", "description", "instructions",
                "personality", "allowed_tools", "rag_filter", "model",
                "max_tokens", "temperature", "extra"}
    assert required <= names, f"missing columns: {required - names}"


def test_agent_slugs_prefixed():
    for slug in AGENT_SLUGS:
        assert slug.startswith("sap_hcm_"), f"{slug} not prefixed sap_hcm_"


def test_triggers_present_distinct_spanish():
    triggers = _triggers_by_agent(AGENTS_MIGRATION.read_text(encoding="utf-8"))
    assert len(triggers) == 2, f"expected triggers for 2 agents, got {len(triggers)}"
    a, b = triggers
    assert a and b, "an agent has no triggers"
    assert not (set(a) & set(b)), "agents share trigger phrases"
    for phrase in a + b:
        assert phrase == phrase.lower(), f"trigger not lowercase: {phrase!r}"
        assert " " in phrase, f"trigger should be a phrase: {phrase!r}"


def test_agents_seed_clean_and_no_workspace_id():
    for path in (AGENTS_MIGRATION, CARTRIDGE_SEED):
        sql = path.read_text(encoding="utf-8")
        cols = re.search(r"INSERT INTO agents\s*\(([^)]+)\)", sql)
        assert cols, f"{path.name}: no agents insert column list"
        names = {c.strip() for c in cols.group(1).replace("\n", " ").split(",")}
        assert "workspace_id" not in names, f"{path.name}: agents table has no workspace_id column"
        assert not FORBIDDEN_SQL.search(sql), f"{path.name}: trips P6 seed blacklist"


def test_agents_migration_scope():
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    sql = AGENTS_MIGRATION.read_text(encoding="utf-8")
    stmts = [s for s in sqlglot.parse(sql, read="postgres") if s]
    assert stmts, "no statements parsed"
    targets = set()
    for stmt in stmts:
        assert isinstance(stmt, exp.Insert), f"non-INSERT: {type(stmt).__name__}"
        tbl = stmt.find(exp.Table)
        targets.add(tbl.name if tbl else None)
    assert targets == {"agents", "schema_migrations"}, f"unexpected targets: {targets}"


def test_no_other_cartridge_referenced():
    for path in (AGENTS_MIGRATION, CARTRIDGE_SEED):
        sql = path.read_text(encoding="utf-8").lower()
        block = sql.split("insert into agents", 1)[-1]
        for other in ("replicon", "sap_s4hana", "sap_successfactors"):
            assert other not in block, f"{path.name}: agents block references {other}"


def test_hcm_inventory_blocks_a_to_e():
    kb_ids = _kb_ids()
    assert len(kb_ids) == 12, f"expected 12 KBs, got {len(kb_ids)}"
    assert len([k for k in kb_ids if k.startswith("kb_sap_hcm_")]) == 7
    sql80 = MIGRATION_80.read_text(encoding="utf-8")
    n_datasets = len(re.findall(r"\$seed\$[a-z0-9_]+\$seed\$,\s*\$seed\$(?:silver|gold)\$seed\$", sql80))
    assert n_datasets == 21, f"expected 21 datasets, got {n_datasets}"
    apps = sorted(p.stem for p in APPS_DIR.glob("*.html"))
    assert apps == ["sap_hcm_headcount_dashboard", "sap_hcm_people_quality_dashboard"]
    assert HINTS.is_file()
    assert len(AGENT_SLUGS) == 2
