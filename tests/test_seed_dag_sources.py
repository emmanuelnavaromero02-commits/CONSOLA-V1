"""Sprint v1.20 — seed cartridge_dags.source_code from on-disk .py files.

Tests cover:
  * _find_dag_source reads a file from a real temp directory injected
    into the module's search path.
  * _find_dag_source returns None when no matching file exists.
  * seed_missing_dag_sources only acts on rows with NULL source_code
    (idempotency on rerun).
  * The UPDATE statement guards on `source_code IS NULL` so a concurrent
    write can't be clobbered.
  * Errors reading individual files don't crash the seeder.

The seed module is import-only — it doesn't touch the database at
module load — so we can import it directly without stubbing asyncpg.
The DB-level tests use AsyncMock to simulate the asyncpg.Pool/connection
contract.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def seed_module(monkeypatch):
    """Load console's app.services.seed_dag_sources fresh."""
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [
        p for p in sys.path
        if "/cartridges/" not in p
        and "/refinement" not in p
        and "/vault" not in p
    ]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    return importlib.import_module("app.services.seed_dag_sources")


# ── _find_dag_source ────────────────────────────────────────────────


def test_find_dag_source_reads_file_from_canonical_dags_subpath(seed_module, tmp_path, monkeypatch):
    """Layout: <root>/<cartridge>/dags/<file>.py — the canonical cartridge
    source-tree shape."""
    (tmp_path / "sap_hcm" / "dags").mkdir(parents=True)
    target = tmp_path / "sap_hcm" / "dags" / "sap_hcm_extract.py"
    target.write_text('@dag(dag_id="sap_hcm_extract")\ndef d(): pass\n')
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])

    src = seed_module._find_dag_source("sap_hcm", "sap_hcm_extract.py")
    assert src is not None
    assert "@dag" in src
    assert 'dag_id="sap_hcm_extract"' in src


def test_find_dag_source_reads_file_from_airflow_layout(seed_module, tmp_path, monkeypatch):
    """Layout: <root>/<cartridge>/<file>.py — the shape airflow uses when
    cartridges are bind-mounted under /opt/airflow/dags/<cartridge>/."""
    (tmp_path / "sap_s4hana").mkdir()
    target = tmp_path / "sap_s4hana" / "sap_s4hana_extract_all.py"
    target.write_text('# airflow mount layout\n@dag()\ndef d(): pass\n')
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])

    src = seed_module._find_dag_source("sap_s4hana", "sap_s4hana_extract_all.py")
    assert src is not None
    assert "airflow mount layout" in src


def test_find_dag_source_returns_none_when_missing(seed_module, tmp_path, monkeypatch):
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])
    assert seed_module._find_dag_source("sap_hcm", "does_not_exist.py") is None


def test_find_dag_source_handles_empty_filename(seed_module):
    """An empty file column shouldn't trigger any disk access — just None."""
    assert seed_module._find_dag_source("sap_hcm", "") is None
    assert seed_module._find_dag_source("sap_hcm", None) is None


def test_find_dag_source_first_path_wins(seed_module, tmp_path, monkeypatch):
    """When two roots both contain the file, the FIRST listed root wins.
    Documents the search precedence: /registry/cartridges takes priority
    over /opt/airflow/dags in production."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "sap_hcm" / "dags").mkdir(parents=True)
    (second / "sap_hcm" / "dags").mkdir(parents=True)
    (first / "sap_hcm" / "dags" / "x.py").write_text("FROM_FIRST")
    (second / "sap_hcm" / "dags" / "x.py").write_text("FROM_SECOND")
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [first, second])
    assert seed_module._find_dag_source("sap_hcm", "x.py") == "FROM_FIRST"


# ── seed_missing_dag_sources — asyncpg pool/connection mock ─────────


def _async_pool_with_rows(rows):
    """Build an AsyncMock pool whose .acquire() yields a connection that
    returns ``rows`` on fetch() and counts execute() calls."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock(return_value="UPDATE 1")

    # asyncpg pool.acquire() returns an async context manager
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__  = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


def test_seed_is_noop_when_no_rows_need_backfill(seed_module):
    pool, conn = _async_pool_with_rows([])
    asyncio.run(seed_module.seed_missing_dag_sources(pool))
    conn.fetch.assert_awaited_once()
    # No UPDATE should fire when fetch() returns an empty set.
    conn.execute.assert_not_called()


def test_seed_updates_rows_with_matching_disk_source(seed_module, tmp_path, monkeypatch):
    (tmp_path / "sap_hcm" / "dags").mkdir(parents=True)
    (tmp_path / "sap_hcm" / "dags" / "sap_hcm_extract.py").write_text("DAG_CODE_HCM")
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])

    rows = [
        {"cartridge_id": "sap_hcm", "dag_id": "sap_hcm_extract",
         "file": "sap_hcm_extract.py"},
    ]
    pool, conn = _async_pool_with_rows(rows)
    asyncio.run(seed_module.seed_missing_dag_sources(pool))

    conn.execute.assert_awaited_once()
    args = conn.execute.call_args[0]
    # First arg is the SQL statement
    sql = args[0]
    assert "UPDATE cartridge_dags" in sql
    assert "source_code IS NULL" in sql, (
        "UPDATE must guard on source_code IS NULL so a concurrent write "
        "can't be clobbered by the seeder"
    )
    # Remaining args are (source_code, cartridge_id, dag_id)
    assert args[1] == "DAG_CODE_HCM"
    assert args[2] == "sap_hcm"
    assert args[3] == "sap_hcm_extract"


def test_seed_skips_rows_without_on_disk_source(seed_module, tmp_path, monkeypatch):
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])
    rows = [
        # File doesn't exist on disk; row should be skipped, not crashed on.
        {"cartridge_id": "sap_hcm", "dag_id": "sap_hcm_extract",
         "file": "missing.py"},
    ]
    pool, conn = _async_pool_with_rows(rows)
    asyncio.run(seed_module.seed_missing_dag_sources(pool))
    # No UPDATE — the row was skipped.
    conn.execute.assert_not_called()


def test_seed_is_idempotent_on_rerun(seed_module, tmp_path, monkeypatch):
    """After a successful first run populates the column, a second run
    sees an empty fetch (because the SQL filters on source_code IS NULL)
    and writes nothing."""
    (tmp_path / "sap_hcm" / "dags").mkdir(parents=True)
    (tmp_path / "sap_hcm" / "dags" / "x.py").write_text("CODE")
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])

    # First call: 1 row needs backfill.
    rows_first = [{"cartridge_id": "sap_hcm", "dag_id": "x", "file": "x.py"}]
    pool, conn = _async_pool_with_rows(rows_first)
    asyncio.run(seed_module.seed_missing_dag_sources(pool))
    assert conn.execute.await_count == 1

    # Second call: simulate the post-update state — fetch returns [] because
    # the WHERE source_code IS NULL filter eliminates the now-populated row.
    pool2, conn2 = _async_pool_with_rows([])
    asyncio.run(seed_module.seed_missing_dag_sources(pool2))
    conn2.execute.assert_not_called()


def test_seed_skips_row_with_null_file_column(seed_module, tmp_path, monkeypatch):
    """The fetch SQL also filters `file IS NOT NULL`, but in case a future
    query change loosens that, the in-process guard inside _find_dag_source
    must also handle None gracefully."""
    monkeypatch.setattr(seed_module, "_DAG_SEARCH_PATHS", [tmp_path])
    rows = [
        {"cartridge_id": "sap_hcm", "dag_id": "x", "file": None},
    ]
    pool, conn = _async_pool_with_rows(rows)
    asyncio.run(seed_module.seed_missing_dag_sources(pool))
    conn.execute.assert_not_called()


# ── SQL contract: the fetch query filters out already-populated rows ─


def test_fetch_sql_filters_on_source_code_is_null(seed_module):
    """Read the module source to confirm the SELECT filters rows whose
    source_code is already populated. This is the keystone of the
    idempotency guarantee — if a future edit drops the WHERE clause,
    the seeder would re-encode every row on every boot."""
    src = (
        REPO_ROOT / "console" / "app" / "services" / "seed_dag_sources.py"
    ).read_text(encoding="utf-8")
    assert "WHERE source_code IS NULL" in src, (
        "the SELECT must filter on source_code IS NULL to guarantee idempotency"
    )
    assert "AND file IS NOT NULL" in src, (
        "the SELECT must also skip rows with a NULL file column"
    )
