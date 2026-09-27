from __future__ import annotations

import asyncio
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest


CONSOLE = Path(__file__).resolve().parents[1]
WORKSPACES = [
    {"workspace_id": "22222222-2222-2222-2222-222222222222", "tenant_id": "11111111-1111-1111-1111-111111111111"},
    {"workspace_id": "44444444-4444-4444-4444-444444444444", "tenant_id": "33333333-3333-3333-3333-333333333333"},
]


def _janitor():
    return import_module("app.services.pipeline_run_janitor")


class _Ctx:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_exc):
        return False


class LockConn:
    def __init__(self, leader: bool):
        self.leader = leader
        self.statements: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args))
        return self.leader

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "OK"


class PlatformConn:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        return _Ctx(self)

    async def execute(self, sql, *args):
        self.pool.platform_statements.append(sql)
        return "OK"

    async def fetch(self, sql, *args):
        self.pool.platform_statements.append(sql)
        return await self.pool.fetch(sql, *args)


class Pool:
    def __init__(self, leader: bool, workspaces=WORKSPACES):
        self.lock_conn = LockConn(leader)
        self.workspaces = workspaces
        self.fetches: list[str] = []
        self.platform_statements: list[str] = []
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        return _Ctx(self.lock_conn if self.acquired == 1 else PlatformConn(self))

    async def fetch(self, sql, *args):
        self.fetches.append(sql)
        return [dict(row) for row in self.workspaces]


def _pool_getter(pool):
    async def get_pool():
        return pool

    return get_pool


def test_modes_and_interval_defaults(monkeypatch):
    janitor = _janitor()
    monkeypatch.delenv("PIPELINE_RUN_JANITOR_MODE", raising=False)
    monkeypatch.delenv("PIPELINE_RUN_JANITOR_INTERVAL_SECONDS", raising=False)
    assert janitor.janitor_mode() == "sync"
    assert janitor.janitor_interval_seconds() == 300
    monkeypatch.setenv("PIPELINE_RUN_JANITOR_MODE", "RECOVER")
    assert janitor.janitor_mode() == "recover"
    monkeypatch.setenv("PIPELINE_RUN_JANITOR_MODE", "delete-everything")
    assert janitor.janitor_mode() == "sync"
    monkeypatch.setenv("PIPELINE_RUN_JANITOR_INTERVAL_SECONDS", "5")
    assert janitor.janitor_interval_seconds() == 60


def test_sync_mode_only_syncs_airflow_terminal_runs():
    janitor = _janitor()
    assert janitor.janitor_allowed_classes("sync", neutralize=True) == frozenset({"airflow_terminal"})
    assert janitor.janitor_allowed_classes("recover", neutralize=False) == frozenset({"airflow_terminal", "missing_in_airflow"})
    assert {
        "stalled_queued_paused_dag",
        "stalled_queued_no_progress",
        "stalled_running_no_tasks",
    } <= janitor.janitor_allowed_classes("recover", neutralize=True)


def test_follower_skips_without_reading_workspaces():
    janitor = _janitor()
    pool = Pool(leader=False)
    called = []

    async def recover(*_args, **_kwargs):
        called.append(True)

    result = asyncio.run(janitor.run_janitor_tick(mode="sync", get_db_pool=_pool_getter(pool), recover=recover))
    assert result == {"status": "skipped", "reason": "not_leader", "mode": "sync"}
    assert pool.fetches == []
    assert called == []
    assert "pg_try_advisory_lock(hashtext($1))" in pool.lock_conn.statements[0][0]
    assert pool.lock_conn.statements[0][1] == ("omega:pipeline_run_janitor",)


def test_leader_visits_each_workspace_as_the_system_actor_and_unlocks():
    janitor = _janitor()
    pool = Pool(leader=True)
    calls: list[dict] = []

    async def recover(user, **kwargs):
        calls.append({"user": user, **kwargs})
        if user["workspace_id"] == WORKSPACES[0]["workspace_id"]:
            raise RuntimeError("airflow unavailable")
        return SimpleNamespace(counts={"recovered": 0, "synced_terminal": 2})

    result = asyncio.run(
        janitor.run_janitor_tick(
            mode="sync",
            get_db_pool=_pool_getter(pool),
            recover=recover,
            neutralize=True,
            threshold_seconds=900,
        )
    )
    assert result["status"] == "partial"
    assert result["workspaces"] == 2
    assert result["synced_terminal"] == 2
    assert result["failed_workspaces"] == 1
    assert [call["user"]["workspace_id"] for call in calls] == [row["workspace_id"] for row in WORKSPACES]
    for call in calls:
        assert call["user"]["email"] == "system:pipeline-janitor"
        assert call["actor"] == "system:pipeline-janitor"
        assert call["mode"] == "apply"
        assert call["allowed_classes"] == frozenset({"airflow_terminal"})
        assert call["neutralize_airflow"] is False
    assert "pg_advisory_unlock(hashtext($1))" in pool.lock_conn.statements[-1][0]


def test_recover_mode_neutralizes_only_when_enabled():
    janitor = _janitor()
    seen: list[dict] = []

    async def recover(_user, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(counts={"recovered": 1, "synced_terminal": 0})

    asyncio.run(janitor.run_janitor_tick(mode="recover", get_db_pool=_pool_getter(Pool(True, WORKSPACES[:1])), recover=recover, neutralize=False))
    asyncio.run(janitor.run_janitor_tick(mode="recover", get_db_pool=_pool_getter(Pool(True, WORKSPACES[:1])), recover=recover, neutralize=True))
    assert seen[0]["neutralize_airflow"] is False
    assert "stalled_queued_paused_dag" not in seen[0]["allowed_classes"]
    assert seen[1]["neutralize_airflow"] is True
    assert "stalled_queued_paused_dag" in seen[1]["allowed_classes"]


def test_unlock_happens_even_if_the_workspace_query_fails():
    janitor = _janitor()

    class BrokenPool(Pool):
        async def fetch(self, sql, *args):
            raise RuntimeError("database gone")

    pool = BrokenPool(leader=True)
    with pytest.raises(RuntimeError):
        asyncio.run(janitor.run_janitor_tick(mode="sync", get_db_pool=_pool_getter(pool)))
    assert "pg_advisory_unlock" in pool.lock_conn.statements[-1][0]


def test_off_mode_never_ticks(monkeypatch):
    janitor = _janitor()
    monkeypatch.setenv("PIPELINE_RUN_JANITOR_MODE", "off")
    ticks = []

    async def tick():
        ticks.append(True)
        return {}

    asyncio.run(janitor.janitor_loop(tick=tick, initial_delay=0))
    assert ticks == []
    result = asyncio.run(janitor.run_janitor_tick(mode="off"))
    assert result == {"status": "skipped", "reason": "disabled"}


def test_loop_survives_a_failing_tick_and_stops_on_request(monkeypatch):
    janitor = _janitor()
    monkeypatch.setenv("PIPELINE_RUN_JANITOR_MODE", "sync")
    monkeypatch.setattr(janitor, "janitor_interval_seconds", lambda: 0.01)

    async def scenario():
        stop = asyncio.Event()
        ticks = []

        async def tick():
            ticks.append(True)
            if len(ticks) == 1:
                raise RuntimeError("transient")
            if len(ticks) >= 3:
                stop.set()
            return {"recovered": 0}

        await asyncio.wait_for(janitor.janitor_loop(stop, tick=tick, initial_delay=0), 2)
        return ticks

    assert len(asyncio.run(scenario())) == 3


def test_janitor_is_started_and_cancelled_with_the_console_lifespan():
    source = (CONSOLE / "app" / "main.py").read_text(encoding="utf-8")
    lifespan = source.split("async def lifespan(app: FastAPI):", 1)[1].split("INTERNAL_API_KEY = ", 1)[0]
    assert "pipeline_run_janitor.janitor_loop()" in lifespan
    assert "pipeline_janitor_task" in lifespan.split("finally:", 1)[1]


def test_workspace_listing_runs_in_the_system_platform_scope():
    janitor = _janitor()
    pool = Pool(leader=True)

    async def recover(_user, **_kwargs):
        return SimpleNamespace(counts={})

    asyncio.run(janitor.run_janitor_tick(mode="sync", get_db_pool=_pool_getter(pool), recover=recover))
    assert len(pool.platform_statements) == 2
    scope_sql, listing_sql = pool.platform_statements
    assert "set_config('app.platform_admin', 'true', true)" in scope_sql
    assert "set_config('app.workspace_id', '', true)" in scope_sql
    assert "FROM workspaces w" in listing_sql
    source = (CONSOLE / "app" / "services" / "pipeline_run_janitor.py").read_text(encoding="utf-8")
    assert "system_platform_db(pool, purpose=PLATFORM_PURPOSE)" in source
    assert source.count("FROM workspaces") == 1
