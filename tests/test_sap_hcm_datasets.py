"""Phase 2 Block B — SAP HCM silver/gold datasets.

13 silver + 8 gold dataset SQL files in cartridges/sap_hcm/datasets/, registered
in the `datasets` catalog via infra/init/80_sap_hcm_datasets_seed.sql (a
migration, mirroring how Replicon seeds its catalog — the cartridge seed guard
intentionally disallows INSERT INTO datasets / INSERT ... SELECT).

Static checks (no live DuckDB): every file parses, headers are well-formed,
the migration and the files agree, declared sources are real HCM entities, and
no gold exposes an encrypted column raw (privacy by design).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "cartridges" / "sap_hcm" / "datasets"
MIGRATION_80 = REPO_ROOT / "infra" / "init" / "80_sap_hcm_datasets_seed.sql"
ENTITIES_YAML = REPO_ROOT / "cartridges" / "sap_hcm" / "app" / "config" / "entities.yaml"

HEADER_RE = re.compile(r"^--\s+(\S+)\s+\((silver|gold)\)\s+cartridge:\s+sap_hcm\s*$")
EXPECTED_SILVER = 13
EXPECTED_GOLD = 8


def _dataset_files() -> list[Path]:
    return sorted(DATASETS_DIR.glob("*.sql"))


def _hcm_entities() -> set[str]:
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
    assert desc_m, f"{path.name}: missing description line"
    return name, layer, sources, desc_m.group(1)


def test_there_are_21_datasets():
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
        assert sources, f"{path.name}: empty sources"
        assert desc, f"{path.name}: empty description"
        layers.append(layer)
    assert layers.count("silver") == EXPECTED_SILVER
    assert layers.count("gold") == EXPECTED_GOLD


def _migration_rows() -> dict[str, str]:
    sql = MIGRATION_80.read_text(encoding="utf-8")
    return dict(re.findall(r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$", sql))


def test_migration_matches_files():
    mig = _migration_rows()
    files = {p.stem: _parse_header(p)[1] for p in _dataset_files()}
    assert set(mig) == set(files), "migration datasets differ from datasets/*.sql"
    for name, layer in mig.items():
        assert layer == files[name], f"{name}: layer {layer} != file {files[name]}"


def test_migration_is_idempotent_and_scoped():
    sql = MIGRATION_80.read_text(encoding="utf-8")
    assert "ON CONFLICT (name) DO NOTHING" in sql
    assert "'80_sap_hcm_datasets_seed.sql'" in sql
    # Every seeded row's cartridge column is sap_hcm; no other cartridge appears
    # as a quoted catalog value (prose comments may name Replicon as the model).
    assert "$seed$sap_hcm$seed$" in sql
    for other in ("$seed$replicon$seed$", "$seed$sap_successfactors$seed$", "$seed$sap_s4hana$seed$"):
        assert other not in sql, f"migration seeds another cartridge: {other}"


def test_declared_sources_are_real_hcm_entities():
    entities = _hcm_entities()
    assert len(entities) == 10
    for path in _dataset_files():
        _, _, sources, _ = _parse_header(path)
        for src in sources:
            m = re.match(r"raw/sap_hcm/(\w+)$", src)
            assert m, f"{path.name}: malformed source {src!r}"
            assert m.group(1) in entities, f"{path.name}: source {src!r} not a HCM entity"


def test_golds_do_not_expose_encrypted_columns():
    # Privacy by design: birth date (Gbdat) is encrypted in bronze; no gold may
    # surface it raw or under its silver alias.
    for path in _dataset_files():
        _, layer, _, _ = _parse_header(path)
        if layer != "gold":
            continue
        body = path.read_text(encoding="utf-8").lower()
        assert "gbdat" not in body, f"{path.name} references encrypted Gbdat"
        assert "birth_date" not in body, f"{path.name} surfaces birth_date"
