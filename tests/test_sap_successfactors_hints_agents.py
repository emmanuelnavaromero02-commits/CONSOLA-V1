"""Phase 2 Block E3 — SAP SuccessFactors assistant hints + specialized agents.

Parallel to the HCM (#196) and S4 (#199) hints/agents tests. hints/assistant.md
feeds cartridges.assistant_hints (injected into the copilot prompt, capped at 8000
chars). 2 agents are seeded into the `agents` table via migration 88, mirrored in
config/seed.sql. No workspace_id, no triggers column (triggers live in extra);
instructions must avoid the P6 seed-hardening blacklist.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HINTS = REPO_ROOT / "cartridges" / "sap_successfactors" / "hints" / "assistant.md"
AGENTS_MIGRATION = REPO_ROOT / "infra" / "init" / "88_sap_successfactors_agents_seed.sql"
CARTRIDGE_SEED = REPO_ROOT / "cartridges" / "sap_successfactors" / "config" / "seed.sql"
KBS_YAML = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "knowledge_bits.yaml"
MIGRATION_82 = REPO_ROOT / "infra" / "init" / "82_sap_successfactors_datasets_seed.sql"
MIGRATION_99P = REPO_ROOT / "infra" / "init" / "99p_sap_successfactors_talent_datasets.sql"
DATASETS_DIR = REPO_ROOT / "cartridges" / "sap_successfactors" / "datasets"
APPS_DIR = REPO_ROOT / "cartridges" / "sap_successfactors" / "apps"

MAX_HINTS_CHARS = 8000
AGENT_SLUGS = ["sap_successfactors_hr_strategist", "sap_successfactors_talent_advisor"]
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


def _dataset_names() -> set[str]:
    header = re.compile(r"^--\s+([a-z0-9_]+)\s+\((silver|gold)\)\s+cartridge:\s+sap_successfactors")
    names: set[str] = set()
    for path in DATASETS_DIR.glob("*.sql"):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        match = header.match(first)
        if match:
            names.add(match.group(1))
    return names


def _triggers_by_agent(sql: str) -> list[list[str]]:
    return [json.loads(m) for m in re.findall(r'"triggers"\s*:\s*(\[[^\]]*\])', sql)]


def test_hints_exist_and_markdown():
    assert HINTS.is_file(), "hints/assistant.md missing"
    text = _hints()
    assert text.lstrip().startswith("#")
    assert text.count("##") >= 6


def test_hints_has_six_sections():
    text = _hints()
    for section in REQUIRED_SECTIONS:
        assert section in text, f"hints missing section: {section!r}"


def test_hints_under_injection_cap():
    text = _hints()
    assert len(text) <= MAX_HINTS_CHARS, f"hints {len(text)} chars exceed cap {MAX_HINTS_CHARS}"
    for token in ("</hints_cartucho>", "<system>", "<|im_start|>"):
        assert token not in text


def test_hints_references_existing_kbs():
    referenced = set(re.findall(r"kb_sap_successfactors_[a-z0-9_]+", _hints()))
    assert referenced, "hints references no kb_sap_successfactors_* KB"
    real = _kb_ids()
    for kb in referenced:
        assert kb in real, f"hints references unknown KB {kb!r}"


def test_hints_references_existing_golds():
    # gold table names in hints look like gold_sap_successfactors_<x>; the dataset
    # name is sap_successfactors_<x> (the gold_ prefix is the pggold table prefix).
    referenced = set(re.findall(r"gold_(sap_successfactors_[a-z0-9_]+)", _hints()))
    assert referenced, "hints references no gold dataset"
    datasets = _dataset_names()
    for name in referenced:
        assert name in datasets, f"hints references unknown dataset {name}"


def test_hints_references_existing_apps():
    referenced = set(re.findall(r"sap_successfactors_(?:workforce_overview|talent_health)", _hints()))
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
    assert cols
    names = {c.strip() for c in cols.group(1).replace("\n", " ").split(",")}
    required = {"cartridge_id", "slug", "name", "description", "instructions",
                "personality", "allowed_tools", "rag_filter", "model",
                "max_tokens", "temperature", "extra"}
    assert required <= names, f"missing columns: {required - names}"


def test_agent_slugs_prefixed():
    for slug in AGENT_SLUGS:
        assert slug.startswith("sap_successfactors_"), f"{slug} not prefixed"


def test_triggers_present_distinct():
    triggers = _triggers_by_agent(AGENTS_MIGRATION.read_text(encoding="utf-8"))
    assert len(triggers) == 2, f"expected triggers for 2 agents, got {len(triggers)}"
    a, b = triggers
    assert a and b
    assert not (set(a) & set(b)), "agents share trigger phrases"
    for phrase in a + b:
        assert phrase == phrase.lower(), f"trigger not lowercase: {phrase!r}"


def test_agents_seed_clean_and_no_workspace_id():
    for path in (AGENTS_MIGRATION, CARTRIDGE_SEED):
        sql = path.read_text(encoding="utf-8")
        cols = re.search(r"INSERT INTO agents\s*\(([^)]+)\)", sql)
        assert cols, f"{path.name}: no agents insert column list"
        names = {c.strip() for c in cols.group(1).replace("\n", " ").split(",")}
        assert "workspace_id" not in names, f"{path.name}: agents has no workspace_id column"
        assert not FORBIDDEN_SQL.search(sql), f"{path.name}: trips P6 seed blacklist"


def test_agents_migration_scope():
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    sql = AGENTS_MIGRATION.read_text(encoding="utf-8")
    targets = set()
    for stmt in (s for s in sqlglot.parse(sql, read="postgres") if s):
        assert isinstance(stmt, exp.Insert), f"non-INSERT: {type(stmt).__name__}"
        tbl = stmt.find(exp.Table)
        targets.add(tbl.name if tbl else None)
    assert targets == {"agents", "schema_migrations"}, f"unexpected targets: {targets}"


# Holistic SuccessFactors inventory (Blocks A-E + Talent/WB-TALENTO).
def test_sf_inventory_blocks_a_to_e():
    kb_ids = _kb_ids()
    assert len(kb_ids) == 27, f"expected 27 KBs, got {len(kb_ids)}"
    assert len([k for k in kb_ids if k.startswith("kb_sap_successfactors_")]) == 21
    n_datasets = len(_dataset_names())
    assert n_datasets == 108, f"expected 108 datasets, got {n_datasets}"
    apps = sorted(p.stem for p in APPS_DIR.glob("*.html"))
    assert apps == ["sap_successfactors_talent_health", "sap_successfactors_workforce_overview"]
    assert HINTS.is_file()
    assert len(AGENT_SLUGS) == 2
