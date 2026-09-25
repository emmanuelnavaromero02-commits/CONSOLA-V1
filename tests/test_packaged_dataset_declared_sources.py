from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS = sorted((REPO_ROOT / "cartridges").glob("*/datasets/*.sql"))
SOURCES_RE = re.compile(r"^-- sources:\s*(\[.*\])\s*$", re.M)
READ_RE = re.compile(r"s3://\{bucket\}/((?:raw|silver|gold)/[^/'*]+/[^/'*]+)")


def test_every_cartridge_ships_datasets():
    assert len({path.parts[-3] for path in DATASETS}) >= 9


@pytest.mark.parametrize("path", DATASETS, ids=lambda path: f"{path.parts[-3]}/{path.stem}")
def test_every_lakehouse_path_a_dataset_reads_is_declared(path):
    sql = path.read_text(encoding="utf-8")
    match = SOURCES_RE.search(sql)
    declared = json.loads(match.group(1)) if match else []
    reads = set(READ_RE.findall(sql))

    assert len(declared) == len(set(declared))
    assert reads <= set(declared), sorted(reads - set(declared))
