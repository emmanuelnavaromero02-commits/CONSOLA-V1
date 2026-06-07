"""Phase 2 Block B — SAP SuccessFactors silver/gold datasets.

24 silver + 8 gold dataset SQL files in cartridges/sap_successfactors/datasets/,
registered in the `datasets` catalog via
infra/init/82_sap_successfactors_datasets_seed.sql (a migration, mirroring the
HCM/S4 datasets seeds).

All dataset names are prefixed `sap_successfactors_` because `datasets.name` is a
global primary key (headcount_by_department / manager_hierarchy /
employees_anomalies already exist for sap_hcm).

Static checks: every file parses, headers well-formed, migration<->files agree
(incl. workspace_id on every row), sources are real SF entities, prefix avoids PK
collisions, and no gold exposes an encrypted column.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "cartridges" / "sap_successfactors" / "datasets"
MIGRATION = REPO_ROOT / "infra" / "init" / "82_sap_successfactors_datasets_seed.sql"
ENTITIES_YAML = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "entities.yaml"

HEADER_RE = re.compile(r"^--\s+(\S+)\s+\((silver|gold)\)\s+cartridge:\s+sap_successfactors\s*$")
EXPECTED_SILVER = 24
EXPECTED_GOLD = 8
ENCRYPTED_FIELDS = ("paycomp_value", "date_of_birth", "national_id")
DEDUP_LATEST_KEYS = {
    "sap_successfactors_perperson_latest.sql": ("personIdExternal",),
    "sap_successfactors_perpersonal_latest.sql": ("personIdExternal", "startDate"),
    "sap_successfactors_peremail_latest.sql": ("personIdExternal", "emailType", "emailAddress"),
    "sap_successfactors_empemployment_latest.sql": ("personIdExternal", "userId", "startDate"),
    "sap_successfactors_empjob_latest.sql": ("userId", "startDate"),
    "sap_successfactors_paymentinformationdetailv3_latest.sql": ("externalCode",),
    "sap_successfactors_folocation_latest.sql": ("externalCode",),
    "sap_successfactors_focompany_latest.sql": ("externalCode",),
    "sap_successfactors_fodepartment_latest.sql": ("externalCode",),
    "sap_successfactors_fodivision_latest.sql": ("externalCode",),
    "sap_successfactors_fobusinessunit_latest.sql": ("externalCode",),
    "sap_successfactors_fojobcode_latest.sql": ("externalCode",),
    "sap_successfactors_empemploymenttermination_latest.sql": (
        "userId",
        "endDate",
        "eventReasonExternalCode",
    ),
}


def _dataset_files() -> list[Path]:
    return sorted(DATASETS_DIR.glob("*.sql"))


def _sf_entities() -> set[str]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8")) or {}
    return {e["entity"] for e in data.get("entities", [])}


def _parse_header(path: Path) -> tuple[str, str, list[str], str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    m = HEADER_RE.match(lines[0])
    assert m, f"{path.name}: bad header line 1: {lines[0]!r}"
    name, layer = m.group(1), m.group(2)
    src_m = re.match(r"^--\s+sources:\s+(\[.*\])\s*$", lines[1])
    assert src_m, f"{path.name}: bad sources line: {lines[1]!r}"
    sources = json.loads(src_m.group(1))
    desc_m = re.match(r"^--\s+description:\s+(.*\S)\s*$", lines[2])
    assert desc_m, f"{path.name}: missing description"
    return name, layer, sources, desc_m.group(1)


def test_there_are_32_datasets():
    assert len(_dataset_files()) == EXPECTED_SILVER + EXPECTED_GOLD


def test_all_dataset_sql_parse():
    sqlglot = pytest.importorskip("sqlglot")
    for path in _dataset_files():
        stmts = [s for s in sqlglot.parse(path.read_text(encoding="utf-8"), read="duckdb") if s]
        assert stmts, f"{path.name}: no parseable statement"


def test_headers_well_formed_and_match_filename():
    layers: list[str] = []
    for path in _dataset_files():
        name, layer, sources, desc = _parse_header(path)
        assert name == path.stem, f"{path.name}: header name {name!r} != filename"
        assert sources and desc, f"{path.name}: empty sources/description"
        layers.append(layer)
    assert layers.count("silver") == EXPECTED_SILVER
    assert layers.count("gold") == EXPECTED_GOLD


def test_all_names_prefixed_to_avoid_pk_collision():
    # datasets.name is a global PK; SF names must be cartridge-prefixed so they
    # don't collide with sap_hcm's headcount_by_department / manager_hierarchy / etc.
    for path in _dataset_files():
        assert path.stem.startswith("sap_successfactors_"), f"{path.name} not prefixed"


def _migration_rows() -> dict[str, str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    return dict(re.findall(r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$", sql))


def test_migration_matches_files():
    mig = _migration_rows()
    files = {p.stem: _parse_header(p)[1] for p in _dataset_files()}
    assert set(mig) == set(files), "migration datasets differ from datasets/*.sql"
    for name, layer in mig.items():
        assert layer == files[name], f"{name}: layer {layer} != file {files[name]}"


def test_migration_sets_workspace_id_on_every_row():
    sql = MIGRATION.read_text(encoding="utf-8")
    rows = sql.count("$seed$sap_successfactors$seed$")
    ws = sql.count("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1")
    assert rows == EXPECTED_SILVER + EXPECTED_GOLD, f"expected 30 rows, got {rows}"
    assert ws == rows, f"workspace_id missing on some rows: {ws} of {rows}"


def test_migration_idempotent_and_scoped():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ON CONFLICT (name) DO NOTHING" in sql
    assert "'82_sap_successfactors_datasets_seed.sql'" in sql
    for other in ("$seed$replicon$seed$", "$seed$sap_hcm$seed$", "$seed$sap_s4hana$seed$"):
        assert other not in sql, f"migration seeds another cartridge: {other}"


def test_live_successfactors_silver_entities_present():
    files = {p.name for p in _dataset_files()}
    for filename in DEDUP_LATEST_KEYS:
        assert filename in files


def test_live_successfactors_latest_deduplicates_by_business_key():
    for filename, keys in DEDUP_LATEST_KEYS.items():
        sql = (DATASETS_DIR / filename).read_text(encoding="utf-8")
        assert "ROW_NUMBER() OVER" in sql, f"{filename}: missing window dedupe"
        assert "WHERE _rn = 1" in sql, f"{filename}: missing latest row filter"
        assert "MAX(load_date)" not in sql, f"{filename}: still dedupes only by load_date"
        partition = re.search(r"PARTITION BY\s+(.+?)\s+ORDER BY", sql, re.DOTALL)
        assert partition, f"{filename}: missing partition key"
        partition_sql = partition.group(1)
        for key in keys:
            assert key in partition_sql, f"{filename}: missing dedupe key {key}"
        assert "_extracted_at" in sql and "batch_id" in sql, f"{filename}: missing batch recency tie-breakers"


def test_declared_sources_are_real_sf_entities():
    entities = _sf_entities()
    assert len(entities) == 31
    dataset_names = {p.stem for p in _dataset_files()}
    for path in _dataset_files():
        _, _, sources, _ = _parse_header(path)
        for src in sources:
            raw = re.match(r"raw/sap_successfactors/(\w+)$", src)
            if raw:
                assert raw.group(1) in entities, f"{path.name}: source {src!r} not a SF entity"
                continue
            silver = re.match(r"silver/sap_successfactors/([a-z0-9_]+)$", src)
            assert silver, f"{path.name}: malformed source {src!r}"
            assert silver.group(1) in dataset_names, f"{path.name}: source {src!r} not a packaged dataset"


def test_golds_do_not_expose_encrypted_columns():
    # Privacy by design: paycompValue / dateOfBirth / nationalId are encrypted in
    # bronze; no gold may surface them (raw or under their silver alias).
    for path in _dataset_files():
        _, layer, _, _ = _parse_header(path)
        if layer != "gold":
            continue
        body = path.read_text(encoding="utf-8").lower()
        for field in ENCRYPTED_FIELDS:
            assert field not in body, f"{path.name} references encrypted field {field}"
