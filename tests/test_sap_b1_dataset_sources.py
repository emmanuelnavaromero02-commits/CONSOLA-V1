from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS = sorted((REPO_ROOT / "cartridges" / "sap_b1" / "datasets").glob("*.sql"))
SOURCES_RE = re.compile(r"^-- sources:\s*(\[.*\])\s*$", re.M)
READ_RE = re.compile(r"s3://\{bucket\}/((?:raw|silver|gold)/[^/'*]+/[^/'*]+)")


@pytest.mark.parametrize("path", DATASETS, ids=lambda path: path.stem)
def test_declared_sources_are_exactly_what_the_sql_reads(path):
    sql = path.read_text(encoding="utf-8")
    declared = json.loads(SOURCES_RE.search(sql).group(1))

    assert len(declared) == len(set(declared))
    assert set(declared) == set(READ_RE.findall(sql))


@pytest.mark.parametrize(
    "path",
    [path for path in DATASETS if "(gold)" in path.read_text(encoding="utf-8").splitlines()[0]],
    ids=lambda path: path.stem,
)
def test_gold_reads_silver_of_its_own_cartridge(path):
    declared = json.loads(SOURCES_RE.search(path.read_text(encoding="utf-8")).group(1))

    assert declared
    assert all(source.startswith("silver/sap_b1/") for source in declared)
