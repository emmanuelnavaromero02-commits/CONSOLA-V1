from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CONSOLE_DIR = REPO / "console"


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, str, tuple]] = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql: str, *params):
        self.calls.append(("execute", sql, params))

    async def fetch(self, sql: str, *params):
        self.calls.append(("fetch", sql, params))
        return [{"marker": "ok"}]


class _AcquireContext:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConnection()

    def acquire(self):
        return _AcquireContext(self.conn)


@pytest.mark.asyncio
async def test_scoped_db_sets_transaction_local_gucs_before_work():
    sys.path.insert(0, str(CONSOLE_DIR))
    try:
        from app.services.db_scope import run_with_db_scope
    finally:
        sys.path.pop(0)

    pool = _FakePool()

    async def _work(conn, _tenant_id, _workspace_id):
        return await conn.fetch("SELECT marker FROM decisions WHERE workspace_id = $1", "ws-a")

    rows = await run_with_db_scope(
        pool,
        {"tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        _work,
    )

    assert rows == [{"marker": "ok"}]
    assert pool.conn.calls[0][0] == "execute"
    assert "set_config('app.tenant_id'" in pool.conn.calls[0][1]
    assert "set_config('app.workspace_id'" in pool.conn.calls[0][1]
    assert pool.conn.calls[0][2] == ("tenant-a", "ws-a")
    assert pool.conn.calls[1][0] == "fetch"


def test_control_room_uses_central_scope_helper_not_session_guc_fallback():
    execution_src = (REPO / "console/app/services/control_room/execution.py").read_text()
    core_src = (REPO / "console/app/services/control_room/core.py").read_text()

    assert "from app.services.db_scope import" in core_src
    assert "run_with_db_scope" in core_src
    assert "return await run_with_db_scope(pool, user, work)" in execution_src
    assert "set_config('app.tenant_id', $1, false)" not in execution_src


def test_intelligence_aliases_central_scoped_db_helper():
    for rel in (
        "console/app/services/intelligence/history.py",
        "console/app/services/intelligence/persistence.py",
    ):
        src = (REPO / rel).read_text()
        assert "from app.services.db_scope import scoped_db" in src
        assert "async def scoped_db(" not in src


def test_refinement_dataset_store_sets_guc_and_callers_pass_scope():
    store_src = (REPO / "refinement/app/dataset_store.py").read_text()
    main_src = (REPO / "refinement/app/main.py").read_text()

    assert "set_config('app.tenant_id'" in store_src
    assert "set_config('app.workspace_id'" in store_src
    assert "def _dataset_store_scope(" in main_src
    assert "store.list_datasets(**_dataset_store_scope(sec))" in main_src
    assert "store.get_dataset(args[\"name\"], **_dataset_store_scope(sec))" in main_src


def test_decisions_runtime_reads_use_scoped_db_for_user():
    src = (REPO / "console/app/main.py").read_text()

    assert "from app.services.db_scope import scoped_db_for_user" in src
    assert "async with scoped_db_for_user(pool, user)" in src
    assert "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2" in src
