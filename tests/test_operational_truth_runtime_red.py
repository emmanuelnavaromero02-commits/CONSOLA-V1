from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from console.app.services import scheduled_runtime


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, *args):
        self.calls.append((sql, args))

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, scope_rows, agent_rows):
        self.scope_rows = scope_rows
        self.connections = [_Connection(rows) for rows in agent_rows]
        self._index = 0

    async def fetch(self, sql, *_args):
        assert "FROM workspaces" in sql
        return self.scope_rows

    def acquire(self):
        conn = self.connections[self._index]
        self._index += 1
        return _Acquire(conn)


@pytest.mark.asyncio
async def test_due_fanout_enumerates_server_owned_scopes_and_sets_fresh_gucs():
    now = datetime(2026, 7, 30, 12, 5, tzinfo=timezone.utc)
    scopes = [
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
    ]
    rows = [
        [
            {
                "id": "agent-a",
                "cartridge_id": "sap_successfactors",
                "slug": "a",
                "name": "A",
                "extra": {"schedule": {"cron": "4 12 * * *", "enabled": True}},
            }
        ],
        [
            {
                "id": "agent-b",
                "cartridge_id": "sap_successfactors",
                "slug": "b",
                "name": "B",
                "extra": {"schedule": {"cron": "4 12 * * *", "enabled": True}},
            }
        ],
    ]
    pool = _Pool(scopes, rows)

    result = await scheduled_runtime.find_due_agents(
        pool,
        window_start=now - timedelta(minutes=5),
        window_end=now,
    )

    assert result["status"] == "ready"
    assert {row["workspace_id"] for row in result["due"]} == {
        "workspace-a",
        "workspace-b",
    }
    for scope, conn in zip(scopes, pool.connections, strict=True):
        assert "set_config('app.tenant_id'" in conn.calls[0][0]
        assert conn.calls[0][1] == (scope["tenant_id"], scope["workspace_id"])
        query, params = conn.calls[1]
        assert "tenant_id = $1::uuid" in query
        assert "workspace_id = $2::uuid" in query
        assert params == (scope["tenant_id"], scope["workspace_id"])


@pytest.mark.asyncio
async def test_due_fanout_reports_no_eligible_workspaces_without_false_success():
    result = await scheduled_runtime.find_due_agents(
        _Pool([], []),
        window_start=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 30, 12, 5, tzinfo=timezone.utc),
    )

    assert result == {
        "status": "no_eligible_workspaces",
        "due": [],
        "workspaces": 0,
        "failures": [],
    }


def test_due_row_accepts_asyncpg_jsonb_text_without_losing_schedule():
    start = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": "agent-a",
        "cartridge_id": "replicon",
        "slug": "runtime-a",
        "name": "Runtime A",
        "extra": json.dumps(
            {"schedule": {"enabled": True, "cron": "2 12 * * *", "tz": "UTC"}}
        ),
    }

    due = scheduled_runtime._due_row(
        row,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        start=start,
        end=start + timedelta(minutes=5),
    )

    assert due is not None
    assert due["id"] == "agent-a"


def test_airflow_runner_uses_console_owned_fanout_and_sanitized_results():
    source = (
        Path(__file__).resolve().parents[1] / "airflow/dags/agent_runner.py"
    ).read_text(encoding="utf-8")

    assert "/api/operations/internal/agent-runner/due" in source
    assert "FROM agents WHERE is_active" not in source
    assert '"preview":' not in source
    assert '"error": str(exc)' not in source
    assert "no_eligible_workspaces" in source


def test_runtime_window_rejects_unbounded_or_inverted_ranges():
    start = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="window"):
        scheduled_runtime.validate_window(start, start)
    with pytest.raises(ValueError, match="window"):
        scheduled_runtime.validate_window(start, start + timedelta(hours=1))
