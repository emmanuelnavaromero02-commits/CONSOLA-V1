from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGE = REPO_ROOT / "cartridges" / "sap_b1"
DATASETS_DIR = CARTRIDGE / "datasets"
ENTITIES_YAML = CARTRIDGE / "app" / "config" / "entities.yaml"
TOOLS = CARTRIDGE / "tools"

HEADER_RE = re.compile(r"^--\s+(\S+)\s+\((silver|gold)\)\s+cartridge:\s+sap_b1\s*$")
EXPECTED_SILVER = 65
EXPECTED_GOLD = 11
PSEUDO_ENTITIES = {"IntercompanyPartners", "BusinessParameters"}


def _dataset_files() -> list[Path]:
    return sorted(DATASETS_DIR.glob("*.sql"))


def _entities() -> set[str]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8")) or {}
    return {e["entity"] for e in data.get("entities", [])}


def _parse_header(path: Path) -> tuple[str, str, list[str], str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    m = HEADER_RE.match(lines[0])
    assert m, f"{path.name}: bad header line 1: {lines[0]!r}"
    src_m = re.match(r"^--\s+sources:\s+(\[.*\])\s*$", lines[1])
    assert src_m, f"{path.name}: bad sources line: {lines[1]!r}"
    desc_m = re.match(r"^--\s+description:\s+(.*\S)\s*$", lines[2])
    assert desc_m, f"{path.name}: missing description"
    return m.group(1), m.group(2), json.loads(src_m.group(1)), desc_m.group(1)


def _executable_sql(path: Path) -> str:
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_dataset_inventory():
    files = _dataset_files()
    layers = [_parse_header(p)[1] for p in files]
    assert layers.count("silver") == EXPECTED_SILVER, layers.count("silver")
    assert layers.count("gold") == EXPECTED_GOLD, layers.count("gold")
    assert len(files) == EXPECTED_SILVER + EXPECTED_GOLD


def test_all_dataset_sql_parse_as_duckdb():
    sqlglot = pytest.importorskip("sqlglot")
    for path in _dataset_files():
        statements = [s for s in sqlglot.parse(path.read_text(encoding="utf-8"), read="duckdb") if s]
        assert len(statements) == 1, f"{path.name}: expected exactly one statement"


def test_headers_match_filenames_and_sources_are_real_entities():
    entities = _entities() | PSEUDO_ENTITIES
    assert len(_entities()) == 45
    for path in _dataset_files():
        name, layer, sources, description = _parse_header(path)
        assert name == path.stem, f"{path.name}: header name {name!r} != filename"
        assert sources and description
        for source in sources:
            m = re.match(r"^(raw|silver)/sap_b1/([A-Za-z0-9_]+)$", source)
            assert m, f"{path.name}: malformed source {source!r}"
            if m.group(1) == "raw":
                assert m.group(2) in entities, f"{path.name}: source {source!r} is not a sap_b1 entity"
            else:
                assert layer == "gold", f"{path.name}: only gold may declare a silver source"
                upstream = DATASETS_DIR / f"{m.group(2)}.sql"
                assert upstream.exists() and _parse_header(upstream)[1] == "silver", f"{path.name}: {source!r}"


def test_every_read_parquet_is_a_declared_source_or_a_sap_b1_silver():
    for path in _dataset_files():
        _, layer, sources, _ = _parse_header(path)
        body = _executable_sql(path)
        reads = re.findall(r"read_parquet\('s3://\{bucket\}/(raw|silver)/sap_b1/([A-Za-z0-9_]+)/\*\*/\*\.parquet'", body)
        assert reads, f"{path.name}: no read_parquet"
        for kind, name in reads:
            if kind == "raw":
                assert f"raw/sap_b1/{name}" in sources, f"{path.name}: reads raw/sap_b1/{name} without declaring it"
            else:
                assert layer == "gold", f"{path.name}: only gold may read silver outputs"
                assert (DATASETS_DIR / f"{name}.sql").exists(), f"{path.name}: reads unknown silver {name}"
                assert _parse_header(DATASETS_DIR / f"{name}.sql")[1] == "silver"
        assert "hive_partitioning = true" in body or layer == "gold"


def test_incremental_latest_never_collapses_to_the_newest_day():
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8"))
    modes = {e["entity"]: (e.get("mode"), bool(e.get("watermark_field"))) for e in data["entities"]}
    for path in DATASETS_DIR.glob("sap_b1_*_latest.sql"):
        _, _, sources, _ = _parse_header(path)
        entity = sources[0].rsplit("/", 1)[1]
        body = _executable_sql(path)
        assert "MAX(load_date)" not in body, f"{path.name}: MAX(load_date) mixes two runs of the same day"
        if modes[entity][1]:
            assert "ROW_NUMBER() OVER" in body and "PARTITION BY _company" in body, path.name
        else:
            assert "arg_max(regexp_replace(_run_id" in body, f"{path.name}: snapshot must keep the newest run per company"


def test_currency_is_explicit_on_every_amount_dataset():
    for path in _dataset_files():
        body = _executable_sql(path).lower()
        if "amount_local" in body or "revenue" in body or "purchases" in body or "stock_value_local" in body:
            assert "local_currency" in body, f"{path.name}: amounts without local_currency"
        if "amount_sys" in body or "revenue_net_sys" in body:
            assert "sys_currency" in body, f"{path.name}: system-currency amounts without sys_currency"


def test_no_customer_specific_names():
    text = "\n".join(p.read_text(encoding="utf-8") for p in _dataset_files())
    assert not re.search(r"\bSBO[_A-Z0-9-]{3,}\b", text)
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)


def _run_generator(script: str, out_dir: Path) -> None:
    subprocess.run([sys.executable, str(TOOLS / script), str(out_dir)], check=True, capture_output=True, text=True, cwd=REPO_ROOT)


def test_generated_files_are_current():
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        _run_generator("generate_catalog.py", scratch / "catalog")
        assert (scratch / "catalog" / "app" / "config" / "entities.yaml").read_text() == ENTITIES_YAML.read_text()
        assert (scratch / "catalog" / "config" / "seed.sql").read_text() == (CARTRIDGE / "config" / "seed.sql").read_text()
        _run_generator("generate_silver_latest.py", scratch / "silver")
        _run_generator("generate_document_lines.py", scratch / "silver")
        for generated in sorted((scratch / "silver").glob("*.sql")):
            committed = DATASETS_DIR / generated.name
            assert committed.exists(), generated.name
            assert generated.read_text() == committed.read_text(), f"{generated.name} differs from its generator"
