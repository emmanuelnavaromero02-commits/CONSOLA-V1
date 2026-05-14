"""Sprint v1.32 — pggold RLS must cover every UNION/INTERSECT/EXCEPT branch."""
from __future__ import annotations

from unittest.mock import MagicMock

from refinement.app.duckdb_engine import DuckDBEngine


def _engine_with_tenant_columns() -> DuckDBEngine:
    engine = DuckDBEngine()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [("tenant_id", "varchar")]
    engine._conn = MagicMock(return_value=mock_conn)
    return engine


def test_union_bypass_attempt_filters_every_pggold_branch():
    engine = _engine_with_tenant_columns()
    attack_sql = (
        "SELECT * FROM pggold.t1 "
        "UNION "
        "SELECT * FROM pggold.t2 WHERE tenant_id = ?"
    )

    rls_sql, params = engine.get_rls_filters(attack_sql, {"tenant_id": "tenant-safe"})

    assert rls_sql.count("FROM (SELECT * FROM pggold.") == 2, rls_sql
    assert rls_sql.count("tenant_id = ?") >= 2, rls_sql
    assert params == ["tenant-safe", "tenant-safe"]


def test_set_operations_filter_every_pggold_branch():
    for op in ("UNION ALL", "INTERSECT", "EXCEPT"):
        engine = _engine_with_tenant_columns()

        rls_sql, params = engine.get_rls_filters(
            f"SELECT * FROM pggold.t1 {op} SELECT * FROM pggold.t2",
            {"tenant_id": "tenant-safe"},
        )

        assert rls_sql.count("tenant_id = ?") == 2, rls_sql
        assert params == ["tenant-safe", "tenant-safe"]
