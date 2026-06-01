"""Sprint v1.43.1 — Claude B9: RLS test exercised against a REAL
in-memory DuckDB instead of MagicMock.

The original ``tests/test_rls_union_bypass.py`` mocked the engine's
connection. That validates the AST transformer in isolation but never
proves the rewritten SQL actually parses + runs on DuckDB and that the
filter survives execution. Here we:

  1. Spin up an in-memory DuckDB.
  2. Create a ``pggold`` schema with two tables seeded with rows for
     distinct tenants and workspaces.
  3. Build a real ``DuckDBEngine`` and point its ``_conn`` at the
     in-memory connection.
  4. Run 10+ bypass payloads through ``get_rls_filters`` AND execute
     the rewritten SQL. Each must return ONLY the calling tenant's
     rows — or raise (default-deny on parse failure).
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def engine():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(s in p for s in ("/cartridges/", "/console", "/vault",
                                     "/workspace", "/mcp-infra"))
    ]
    sys.path.insert(0, str(repo))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from refinement.app.duckdb_engine import DuckDBEngine

    con = duckdb.connect()
    con.execute("CREATE SCHEMA pggold")
    con.execute(
        "CREATE TABLE pggold.orders ("
        "  tenant_id VARCHAR, workspace_id VARCHAR, order_id INT, amount DOUBLE"
        ")"
    )
    con.execute(
        "INSERT INTO pggold.orders VALUES "
        "('tenant-a', 'workspace-1', 1, 100), "
        "('tenant-a', 'workspace-1', 2, 200), "
        "('tenant-a', 'workspace-2', 5, 555), "
        "('tenant-b', 'workspace-9', 3, 999), "
        "('tenant-b', 'workspace-9', 4, 888)"
    )
    con.execute(
        "CREATE TABLE pggold.users ("
        "  tenant_id VARCHAR, workspace_id VARCHAR, user_id INT, name VARCHAR"
        ")"
    )
    con.execute(
        "INSERT INTO pggold.users VALUES "
        "('tenant-a', 'workspace-1', 10, 'alice'), "
        "('tenant-a', 'workspace-1', 11, 'bob'), "
        "('tenant-a', 'workspace-2', 12, 'carol'), "
        "('tenant-b', 'workspace-9', 20, 'eve'), "
        "('tenant-b', 'workspace-9', 21, 'mallory')"
    )

    eng = DuckDBEngine()
    eng._conn = lambda: con
    yield eng, con
    con.close()


# Helper: rewrite + execute and return result rows.
def _execute_with_rls(eng_pair, sql, tenant, workspace="workspace-1", extra_params=None):
    eng, con = eng_pair
    rewritten, rls_params = eng.get_rls_filters(
        sql,
        {"tenant_id": tenant, "workspace_id": workspace},
    )
    params = list(rls_params) + list(extra_params or [])
    return con.execute(rewritten, params).fetchall()


# ── Sanity: baseline + isolation works ─────────────────────────────────────

def test_baseline_simple_select_filters_to_tenant(engine):
    rows = _execute_with_rls(engine, "SELECT * FROM pggold.orders", "tenant-a")
    assert len(rows) == 2
    assert {r[0] for r in rows} == {"tenant-a"}
    assert {r[1] for r in rows} == {"workspace-1"}


def test_baseline_other_tenant_sees_other_rows(engine):
    rows = _execute_with_rls(
        engine,
        "SELECT * FROM pggold.orders",
        "tenant-b",
        "workspace-9",
    )
    assert len(rows) == 2
    assert {r[0] for r in rows} == {"tenant-b"}
    assert {r[1] for r in rows} == {"workspace-9"}


def test_same_tenant_other_workspace_blocked(engine):
    rows = _execute_with_rls(engine, "SELECT * FROM pggold.orders", "tenant-a", "workspace-1")
    assert {r[2] for r in rows} == {1, 2}
    assert 5 not in {r[2] for r in rows}


# ── Bypass attempts — every payload must respect the filter ────────────────

def test_union_bypass_blocked(engine):
    """The classic: UNION a second branch to leak the other tenant."""
    rows = _execute_with_rls(
        engine,
        "SELECT * FROM pggold.orders UNION SELECT * FROM pggold.orders",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_union_all_bypass_blocked(engine):
    rows = _execute_with_rls(
        engine,
        "SELECT * FROM pggold.orders UNION ALL SELECT * FROM pggold.orders",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_intersect_bypass_blocked(engine):
    rows = _execute_with_rls(
        engine,
        "SELECT tenant_id, order_id FROM pggold.orders "
        "INTERSECT SELECT tenant_id, order_id FROM pggold.orders",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_except_bypass_blocked(engine):
    rows = _execute_with_rls(
        engine,
        "SELECT tenant_id, order_id FROM pggold.orders "
        "EXCEPT SELECT tenant_id, order_id FROM pggold.orders WHERE order_id < 0",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_subquery_in_in_clause_blocked(engine):
    """A subquery inside IN(…) over a second pggold table must ALSO be
    rewritten — otherwise an attacker reads cross-tenant via the
    subselect path."""
    rows = _execute_with_rls(
        engine,
        "SELECT * FROM pggold.orders "
        "WHERE tenant_id IN (SELECT tenant_id FROM pggold.orders)",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_cte_bypass_blocked(engine):
    """A CTE that selects from a pggold table must be rewritten too."""
    rows = _execute_with_rls(
        engine,
        "WITH cte AS (SELECT * FROM pggold.orders) SELECT * FROM cte",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_cross_join_bypass_blocked(engine):
    """A CROSS JOIN of two pggold tables must filter BOTH sides."""
    rows = _execute_with_rls(
        engine,
        "SELECT o.tenant_id, o.workspace_id, u.tenant_id, u.workspace_id "
        "FROM pggold.orders o CROSS JOIN pggold.users u",
        "tenant-a",
    )
    # Every row must show tenant-a/workspace-1 on BOTH sides.
    assert rows
    for o_t, o_w, u_t, u_w in rows:
        assert o_t == "tenant-a"
        assert o_w == "workspace-1"
        assert u_t == "tenant-a"
        assert u_w == "workspace-1"


def test_case_insensitive_schema_blocked(engine):
    """``PGGOLD`` / ``PgGoLd`` must be normalised — the AST visitor
    matches on lowercase. If it didn't, casing would be a bypass."""
    rows = _execute_with_rls(
        engine,
        "SELECT * FROM PgGoLd.orders",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_inline_comment_does_not_bypass(engine):
    """A SQL comment cannot mask a pggold reference from the parser."""
    rows = _execute_with_rls(
        engine,
        "SELECT * FROM pggold.orders /* comment */ "
        "UNION SELECT * /* x */ FROM pggold.orders",
        "tenant-a",
    )
    assert {r[0] for r in rows} == {"tenant-a"}


def test_lateral_subquery_bypass_blocked(engine):
    """LATERAL allowing a row-correlated read of the second table —
    must rewrite the lateral side too."""
    rows = _execute_with_rls(
        engine,
        "SELECT o.tenant_id, sub.user_id "
        "FROM pggold.orders o, LATERAL (SELECT * FROM pggold.users WHERE tenant_id = o.tenant_id) sub",
        "tenant-a",
    )
    for o_t, _ in rows:
        assert o_t == "tenant-a"


def test_unparseable_sql_default_deny(engine):
    """If sqlglot can't parse the SQL, the AST path must raise — not
    fall through to executing unfiltered. This is the audit's v1.0
    weakness pinned closed."""
    eng, _ = engine
    with pytest.raises(ValueError):
        eng.get_rls_filters("this is not sql at all $$$", {"tenant_id": "tenant-a"})


def test_no_pggold_reference_passes_through(engine):
    """SQL that doesn't touch pggold must NOT be transformed (no
    spurious params, no rewrite). This ensures the RLS engine is
    surgical, not paranoid."""
    eng, _ = engine
    rewritten, params = eng.get_rls_filters(
        "SELECT 1 AS x", {"tenant_id": "tenant-a", "workspace_id": "workspace-1"}
    )
    assert params == []
    # Trip through DuckDB to confirm the rewrite still parses + runs.
    rows = eng._conn().execute(rewritten).fetchall()
    assert rows == [(1,)]
