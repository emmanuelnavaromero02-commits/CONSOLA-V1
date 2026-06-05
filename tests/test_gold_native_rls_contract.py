from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from refinement.app.duckdb_engine import DuckDBEngine


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gold_native_rls_migration_default_denies_legacy_unscoped_tables():
    sql = (REPO_ROOT / "infra" / "init_gold" / "35_gold_native_rls.sql").read_text(encoding="utf-8")

    assert "omega_gold_workspace_matches" in sql
    assert "omega_apply_gold_rls_for_table" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.tenant_id', true)" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "USING (false) WITH CHECK (false)" in sql


def test_gold_dsn_includes_tenant_workspace_options_for_scoped_context(monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://u:p@postgres_gold:5433/modecissions_gold?sslmode=disable")
    engine = DuckDBEngine()

    dsn = engine._pg_gold_dsn(
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
        }
    )

    assert "sslmode=disable" in dsn
    assert "options=" in dsn
    assert "+app.tenant_id" not in dsn
    assert "-c%20app.tenant_id" in dsn
    assert "app.tenant_id" in dsn
    assert "11111111-1111-1111-1111-111111111111" in dsn
    assert "app.workspace_id" in dsn
    assert "22222222-2222-2222-2222-222222222222" in dsn


def test_gold_materialization_requires_tenant_workspace_scope(monkeypatch):
    engine = DuckDBEngine()
    con = MagicMock()
    engine._conn = MagicMock(return_value=con)

    with pytest.raises(ValueError, match="tenant_id and workspace_id"):
        engine.materialize(
            {
                "name": "orders",
                "cartridge": "hubspot",
                "layer": "gold",
                "sql_def": "SELECT 1 AS order_count",
                "sources": [],
            },
            user_context={},
        )

    executed_sql = "\n".join(str(call.args[0]) for call in con.execute.call_args_list if call.args)
    assert "CREATE OR REPLACE TABLE pggold" not in executed_sql


def test_scoped_gold_table_creation_applies_native_rls(monkeypatch):
    engine = DuckDBEngine()
    con = MagicMock()
    applied: list[str] = []
    monkeypatch.setattr(engine, "_gold_table_columns", lambda _con, _table: None)
    monkeypatch.setattr(engine, "_apply_gold_rls", lambda table: applied.append(table))

    engine._ensure_scoped_gold_table(con, "gold_orders", "SELECT 1 AS order_count")

    assert applied == ["gold_orders"]
    executed_sql = "\n".join(str(call.args[0]) for call in con.execute.call_args_list if call.args)
    assert "CREATE TABLE pggold.gold_orders" in executed_sql


def test_gold_write_path_avoids_duckdb_postgres_copy_with_rls():
    src = (REPO_ROOT / "refinement" / "app" / "duckdb_engine.py").read_text(encoding="utf-8")

    assert "def _replace_scoped_gold_rows" in src
    assert "set_config('app.tenant_id'" in src
    assert "execute_values(" in src
    assert "INSERT INTO pggold." not in src
