"""Focused contracts for atomic Copilot refresh persistence and scheduling."""

from __future__ import annotations

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
