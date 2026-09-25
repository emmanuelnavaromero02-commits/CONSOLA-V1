from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from refinement.app.duckdb_engine import DuckDBEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_RE = re.compile(r"^-- sources:\s*(\[.*\])\s*$", re.M)
READ_RE = re.compile(r"s3://\{bucket\}/")
DATASETS = sorted(
    path
    for path in (REPO_ROOT / "cartridges").glob("*/datasets/*.sql")
    if READ_RE.search(path.read_text(encoding="utf-8"))
)
SCOPE = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
}


def _engine(monkeypatch) -> DuckDBEngine:
    engine = DuckDBEngine()
    scope = f"tenant_id={SCOPE['tenant_id']}/workspace_id={SCOPE['workspace_id']}"
    monkeypatch.setattr(
        engine,
        "_latest_materialized_uri",
        lambda layer, cartridge, name, user_context=None: engine._storage_uri(
            f"{layer}/{cartridge}/{name}/{scope}/_snapshots/20260925T000000000000Z-a.parquet"
        ),
    )
    return engine


def test_every_cartridge_with_lakehouse_sql_is_covered():
    assert len({path.parts[-3] for path in DATASETS}) >= 6


@pytest.mark.parametrize("path", DATASETS, ids=lambda path: f"{path.parts[-3]}/{path.stem}")
def test_packaged_dataset_reads_only_its_workspace(monkeypatch, path):
    engine = _engine(monkeypatch)
    sql = path.read_text(encoding="utf-8")
    match = SOURCES_RE.search(sql)
    sources = json.loads(match.group(1)) if match else []

    scoped = engine._scope_storage_sql(engine._inject_bucket(sql), sources, SCOPE)
    engine._validate_scoped_storage_sql(scoped, SCOPE)
    assert f"workspace_id={SCOPE['workspace_id']}" in scoped
