from __future__ import annotations

import asyncio

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, call

import pytest

from app.services import copilot_context_persistence as persistence
from app.services import copilot_context_service as service


TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_A = "22222222-2222-2222-2222-222222222222"
WORKSPACE_B = "33333333-3333-3333-3333-333333333333"


def _actor(workspace_id: str = WORKSPACE_A) -> dict[str, object]:
    return {
        "id": 41,
        "email": "operator@example.com",
        "role": "tenant_admin",
        "active_tenant_id": TENANT_ID,
        "active_workspace_id": workspace_id,
    }


def _snapshot(*, generated_by: str = "manual") -> dict[str, object]:
    return {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_A,
        "status": "ready",
        "summary": {"sources_ready": 2, "sources_total": 2},
        "sources": [],
        "metrics": {},
        "errors": [],
        "generated_by": generated_by,
    }


@pytest.mark.asyncio
async def test_refresh_persistence_uses_one_scoped_connection_for_critical_audit(
    monkeypatch,
) -> None:
    pool = object()
    conn = object()
    insert = AsyncMock(return_value="44444444-4444-4444-4444-444444444444")
    upsert = AsyncMock()
    audit = AsyncMock()

    @asynccontextmanager
    async def fake_scope(actual_pool, tenant_id, workspace_id):
        assert actual_pool is pool
        assert tenant_id == TENANT_ID
        assert workspace_id == WORKSPACE_A
        yield conn

    monkeypatch.setattr(
        persistence.copilot_context_authority,
        "tables_ready",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(persistence, "scoped_db", fake_scope)
    monkeypatch.setattr(persistence, "_insert_snapshot", insert)
    monkeypatch.setattr(persistence, "_upsert_recommendations", upsert)
    monkeypatch.setattr(persistence.audit_service, "record_event", audit)
    actor = _actor()
    snapshot = _snapshot()
    recommendations = [{"fingerprint": "live:new"}]

    snapshot_id = await persistence.persist_refresh(
        pool,
        actor,
        snapshot,
        recommendations,
        ip="127.0.0.1",
        user_agent="atomic-test",
    )

    assert snapshot_id == "44444444-4444-4444-4444-444444444444"
    insert.assert_awaited_once_with(conn, snapshot)
    upsert.assert_awaited_once_with(
        conn,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_A,
        snapshot_id=snapshot_id,
        recommendations=recommendations,
    )
    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["connection"] is conn
    assert kwargs["critical"] is True
    assert kwargs["user_id"] == actor["id"]
    assert kwargs["email"] == actor["email"]
    assert kwargs["ip"] == "127.0.0.1"
    assert kwargs["user_agent"] == "atomic-test"
    assert kwargs["status"] == "success"


@pytest.mark.asyncio
async def test_scheduler_keeps_stable_actor_and_optional_forensics(monkeypatch) -> None:
    class Pool:
        async def fetch(self, _sql, _limit):
            return [
                {"tenant_id": TENANT_ID, "workspace_id": WORKSPACE_A},
                {"tenant_id": TENANT_ID, "workspace_id": WORKSPACE_B},
            ]

    pool = Pool()
    collect = AsyncMock(return_value={"persisted": True})
    monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(service, "_tables_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "collect_workspace_context", collect)

    result = await service.refresh_all_workspaces(limit=2)

    assert result == {"status": "ok", "workspaces": 2, "refreshed": 2, "failed": 0}
    expected_users = [
        service._system_user(TENANT_ID, workspace)
        for workspace in (WORKSPACE_A, WORKSPACE_B)
    ]
    assert collect.await_args_list == [
        call(user, generated_by="scheduler", persist=True) for user in expected_users
    ]
    assert {user["id"] for user in expected_users} == {0}
    assert {user["email"] for user in expected_users} == {"system:copilot-context"}


class _RecordingPool:

    def __init__(self, status: str = "DELETE 12") -> None:
        self.status = status
        self.calls: list[tuple] = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self.status


class _FakeStopEvent:

    def __init__(self, ticks: int = 1) -> None:
        self.ticks = ticks
        self.calls = 0

    async def wait(self) -> None:
        self.calls += 1
        if self.calls <= self.ticks:
            raise asyncio.TimeoutError
        return None


@pytest.mark.asyncio
async def test_purge_always_keeps_the_newest_snapshot_of_every_workspace(
    monkeypatch,
) -> None:
    pool = _RecordingPool("DELETE 14142")
    monkeypatch.setattr(
        persistence.copilot_context_authority,
        "tables_ready",
        AsyncMock(return_value=True),
    )

    result = await persistence.purge_expired_snapshots(pool, retention_days=14)

    assert result == {"status": "ok", "deleted": 14142, "retention_days": 14}
    assert len(pool.calls) == 1
    sql, args = pool.calls[0]
    assert args == (14,)
    normalized = " ".join(sql.split())
    assert "s.id <> ( SELECT x.id" in normalized
    assert "WHERE x.workspace_id = s.workspace_id" in normalized
    assert "ORDER BY x.created_at DESC LIMIT 1" in normalized
    assert "$1::int * INTERVAL '1 day'" in normalized
    assert "14" not in normalized


@pytest.mark.asyncio
async def test_purge_is_disabled_by_a_non_positive_retention(monkeypatch) -> None:
    pool = _RecordingPool()
    tables_ready = AsyncMock(return_value=True)
    monkeypatch.setattr(
        persistence.copilot_context_authority, "tables_ready", tables_ready
    )

    for days in (0, -1, "nonsense"):
        result = await persistence.purge_expired_snapshots(pool, retention_days=days)
        if days == "nonsense":
            assert result["retention_days"] == persistence.DEFAULT_RETENTION_DAYS
        else:
            assert result == {
                "status": "skipped",
                "reason": "retention_disabled",
                "deleted": 0,
            }
    assert len(pool.calls) == 1


@pytest.mark.asyncio
async def test_purge_skips_when_tables_are_missing(monkeypatch) -> None:
    pool = _RecordingPool()
    monkeypatch.setattr(
        persistence.copilot_context_authority,
        "tables_ready",
        AsyncMock(return_value=False),
    )

    result = await persistence.purge_expired_snapshots(pool, retention_days=14)

    assert result == {"status": "skipped", "reason": "tables_missing", "deleted": 0}
    assert pool.calls == []


def test_retention_window_reads_the_env_and_falls_back_to_fourteen_days(
    monkeypatch,
) -> None:
    monkeypatch.delenv("COPILOT_CONTEXT_RETENTION_DAYS", raising=False)
    assert service._retention_days() == 14
    monkeypatch.setenv("COPILOT_CONTEXT_RETENTION_DAYS", "30")
    assert service._retention_days() == 30
    monkeypatch.setenv("COPILOT_CONTEXT_RETENTION_DAYS", "  ")
    assert service._retention_days() == 14
    monkeypatch.setenv("COPILOT_CONTEXT_RETENTION_DAYS", "not-a-number")
    assert service._retention_days() == 14
    monkeypatch.setenv("COPILOT_CONTEXT_RETENTION_DAYS", "0")
    assert service._retention_days() == 0


@pytest.mark.asyncio
async def test_scheduler_tick_purges_even_when_the_refresh_fails(monkeypatch) -> None:
    refresh = AsyncMock(side_effect=RuntimeError("refresh exploded"))
    purge = AsyncMock(return_value={"status": "ok", "deleted": 7, "retention_days": 14})
    monkeypatch.setattr(service, "refresh_all_workspaces", refresh)
    monkeypatch.setattr(service, "purge_expired_snapshots", purge)

    await service.hourly_scheduler(stop_event=_FakeStopEvent(ticks=1))

    refresh.assert_awaited_once()
    purge.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_survives_a_failing_purge(monkeypatch) -> None:
    refresh = AsyncMock(return_value={"status": "ok"})
    purge = AsyncMock(side_effect=RuntimeError("purge exploded"))
    monkeypatch.setattr(service, "refresh_all_workspaces", refresh)
    monkeypatch.setattr(service, "purge_expired_snapshots", purge)

    await service.hourly_scheduler(stop_event=_FakeStopEvent(ticks=2))

    assert refresh.await_count == 2
    assert purge.await_count == 2


@pytest.mark.asyncio
async def test_scheduler_purge_uses_the_configured_window(monkeypatch) -> None:
    pool = _RecordingPool("DELETE 0")
    monkeypatch.setenv("COPILOT_CONTEXT_RETENTION_DAYS", "21")
    monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(
        persistence.copilot_context_authority,
        "tables_ready",
        AsyncMock(return_value=True),
    )

    result = await service.purge_expired_snapshots()

    assert result == {"status": "ok", "deleted": 0, "retention_days": 21}
    assert pool.calls[0][1] == (21,)
