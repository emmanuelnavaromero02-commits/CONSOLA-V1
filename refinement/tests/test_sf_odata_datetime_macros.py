from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from refinement.app.duckdb_engine import (
    _SHARED_MACRO_FILES,
    DuckDBEngine,
    _register_shared_macros,
)
from refinement.app.sql_table_function_policy import validate_table_function_query

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "refinement/app"
SF_DATASETS = REPO_ROOT / "cartridges/sap_successfactors/datasets"
SOURCES_RE = re.compile(r"^-- sources:\s*(\[.*\])\s*$", re.M)
SCOPE = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
}


def _real_duckdb():
    existing = sys.modules.get("duckdb")
    if isinstance(existing, MagicMock) or isinstance(
        getattr(existing, "connect", None), MagicMock
    ):
        sys.modules.pop("duckdb", None)
    return importlib.import_module("duckdb")


MACRO_DATASETS = sorted(
    path
    for path in SF_DATASETS.glob("*.sql")
    if "sf_odata_" in path.read_text(encoding="utf-8")
)


def test_odata_macros_ship_with_the_refinement_image():
    assert "sql/sf_odata_datetime.sql" in _SHARED_MACRO_FILES
    assert (APP_ROOT / "sql/sf_odata_datetime.sql").is_file()


def test_shared_macro_registration_exposes_the_odata_macros():
    con = _real_duckdb().connect()
    try:
        _register_shared_macros(con)
        row = con.execute(
            "SELECT sf_odata_date('/Date(1727740800000)/'), "
            "sf_odata_date_strict(NULL, 'x'), "
            "typeof(sf_odata_timestamp('/Date(0+0000)/'))"
        ).fetchone()
    finally:
        con.close()
    assert str(row[0]) == "2024-10-01"
    assert row[1] is None
    assert row[2] == "TIMESTAMP"


def test_macro_registration_fails_closed_when_the_file_is_missing(monkeypatch):
    monkeypatch.setattr(
        "refinement.app.duckdb_engine._SHARED_MACRO_FILES",
        ("sql/does_not_exist.sql",),
    )
    con = _real_duckdb().connect()
    try:
        with pytest.raises(RuntimeError):
            _register_shared_macros(con)
    finally:
        con.close()


def test_every_successfactors_date_consumer_is_covered():
    assert len(MACRO_DATASETS) >= 63


@pytest.mark.parametrize("path", MACRO_DATASETS, ids=lambda path: path.stem)
def test_odata_macro_calls_pass_the_sql_policy(monkeypatch, path):
    engine = DuckDBEngine()
    scope = f"tenant_id={SCOPE['tenant_id']}/workspace_id={SCOPE['workspace_id']}"
    monkeypatch.setattr(
        engine,
        "_latest_materialized_uri",
        lambda layer, cartridge, name, user_context=None: engine._storage_uri(
            f"{layer}/{cartridge}/{name}/{scope}/_snapshots/20260930T000000000000Z-a.parquet"
        ),
    )
    sql = path.read_text(encoding="utf-8")
    match = SOURCES_RE.search(sql)
    sources = json.loads(match.group(1)) if match else []

    engine._validate_safe_sql(sql)
    scoped = engine._scope_storage_sql(engine._inject_bucket(sql), sources, SCOPE)
    engine._validate_scoped_storage_sql(scoped, SCOPE)
    validate_table_function_query(
        scoped,
        expected_bucket=engine.minio_bucket,
        allow_bucket_placeholder=False,
    )
