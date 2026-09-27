from __future__ import annotations

import pytest

from refinement.app import catalog_copilot_store as store_module
from refinement.app.catalog_copilot_store import CatalogCopilotStore

SEC = {
    "trusted": True,
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "workspace_id": "22222222-2222-4222-8222-222222222222",
}


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = [("subject_kind",), ("subject",)]
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return (1,)


class _Conn:
    def __init__(self, log):
        self.statements = log
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture()
def connections(monkeypatch):
    opened: list[_Conn] = []
    log: list = []

    def connect(*args, **kwargs):
        conn = _Conn(log)
        opened.append(conn)
        return conn

    monkeypatch.setattr(store_module.psycopg2, "connect", connect)
    return opened, log


def test_every_block_scopes_its_transaction(connections):
    opened, log = connections
    store = CatalogCopilotStore("postgresql://x")
    store.load_states(SEC)
    store.annotation_epoch(SEC)
    assert len(opened) == 2
    scopes = [params for sql, params in log if "set_config" in sql]
    assert scopes == [(SEC["tenant_id"], SEC["workspace_id"])] * 2
    assert all(conn.closed for conn in opened)


def test_a_session_reuses_one_connection_without_sharing_transactions(connections):
    opened, log = connections
    store = CatalogCopilotStore("postgresql://x")
    with store.session(SEC):
        store.load_states(SEC)
        store.load_catalog_columns(SEC, ["employees"])
        store.load_edges(SEC, ["employees"])
        with store.session(SEC):
            store.annotation_epoch(SEC)
    assert len(opened) == 1
    assert opened[0].commits == 4
    assert sum("set_config" in sql for sql, _ in log) == 4
    assert opened[0].closed is True
    store.load_states(SEC)
    assert len(opened) == 2


def test_a_session_refuses_another_workspace(connections):
    store = CatalogCopilotStore("postgresql://x")
    other = {**SEC, "workspace_id": "33333333-3333-4333-8333-333333333333"}
    with store.session(SEC):
        with pytest.raises(ValueError):
            store.load_states(other)


def test_scope_is_mandatory(connections):
    opened, _ = connections
    store = CatalogCopilotStore("postgresql://x")
    with pytest.raises(ValueError):
        store.load_states({"trusted": True, "tenant_id": SEC["tenant_id"]})
    with pytest.raises(ValueError):
        with store.session({}):
            pass
    assert opened == []


def test_value_bearing_evidence_and_summaries_never_reach_sql(connections):
    opened, log = connections
    store = CatalogCopilotStore("postgresql://x")
    with pytest.raises(ValueError):
        store.upsert_column_annotations(
            SEC,
            dataset="employees",
            layer="silver",
            cartridge="x",
            annotations=[{"column": "rfc", "evidence": {"basis": [], "values": ["GODE800101AB1"]}}],
        )
    with pytest.raises(ValueError):
        store.save_state(
            SEC,
            {
                "subject_kind": "dataset",
                "subject": "employees",
                "fingerprint": "f",
                "status": "ready",
                "summary": {"samples": ["x"]},
            },
        )
    assert not any("INSERT" in sql for sql, _ in log)
    assert all(conn.rollbacks == 1 for conn in opened)


def test_rejected_or_authored_edges_block_copilot_writes(connections, monkeypatch):
    _, log = connections
    store = CatalogCopilotStore("postgresql://x")
    rows = [
        {"from_dataset": "a", "from_column": "x", "to_dataset": "b", "to_column": "x",
         "origin": "copilot", "status": "rejected"},
        {"from_dataset": "c", "from_column": "y", "to_dataset": "a", "to_column": "y",
         "origin": "manual", "status": "active"},
    ]
    monkeypatch.setattr(store, "load_edges", lambda sec, datasets: rows)
    edges = [
        {"from_dataset": "a", "from_column": "x", "to_dataset": "b", "to_column": "x"},
        {"from_dataset": "a", "from_column": "y", "to_dataset": "c", "to_column": "y"},
        {"from_dataset": "a", "from_column": "z", "to_dataset": "b", "to_column": "z",
         "cardinality": "N:1", "confidence": 0.9, "basis": {"codes": ["name:exact"]}},
    ]
    assert store.upsert_copilot_edges(SEC, edges) == (1, 0)
    inserts = [params for sql, params in log if "INSERT INTO data_relationships" in sql]
    assert [params[1] for params in inserts] == ["z"]
    assert "WHERE data_relationships.origin = 'copilot'" in next(
        sql for sql, _ in log if "INSERT INTO data_relationships" in sql
    )


class _ConflictCursor(_Cursor):
    def execute(self, sql, params=None):
        super().execute(sql, params)
        if "INSERT INTO data_relationships" in sql and params and params[1] == "boom":
            raise store_module.psycopg2.IntegrityError("duplicate key")


class _ConflictConn(_Conn):
    def cursor(self):
        return _ConflictCursor(self)


def test_one_conflicting_edge_is_skipped_inside_a_savepoint(monkeypatch):
    log: list = []
    monkeypatch.setattr(store_module.psycopg2, "connect", lambda *a, **k: _ConflictConn(log))
    store = CatalogCopilotStore("postgresql://x")
    monkeypatch.setattr(store, "load_edges", lambda sec, datasets: [])
    edges = [
        {"from_dataset": "a", "from_column": "boom", "to_dataset": "b", "to_column": "boom"},
        {"from_dataset": "a", "from_column": "ok", "to_dataset": "b", "to_column": "ok"},
    ]
    assert store.upsert_copilot_edges(SEC, edges) == (1, 1)
    statements = [sql for sql, _ in log]
    assert "ROLLBACK TO SAVEPOINT copilot_edge" in statements
    assert statements.count("SAVEPOINT copilot_edge") == 2


def test_retirement_touches_only_the_given_copilot_edges(connections):
    _, log = connections
    store = CatalogCopilotStore("postgresql://x")
    assert store.retire_copilot_edges(SEC, set()) == 0
    store.retire_copilot_edges(SEC, {("a", "x", "b", "x"), ("a", "y", "c", "y")})
    updates = [(sql, params) for sql, params in log if "UPDATE data_relationships" in sql]
    assert len(updates) == 2
    assert all("origin = 'copilot' AND status = 'active'" in sql for sql, _ in updates)
