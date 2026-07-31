from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration_recompute_batch
from app.services.intelligence import calibration_service
from app.services.intelligence.calibration_recompute_batch import load_complete_batch


def _row(row_id: int, *, actual_status: str = "hit") -> dict:
    return {
        "id": row_id,
        "observed_at": row_id,
        "source_type": "manual_fixture",
        "source_id": f"fixture-{row_id}",
        "actual_status": actual_status,
    }


class BatchConnection:
    def __init__(self, rows: list[dict], *, eligible_total: int | None = None) -> None:
        self.rows = rows
        self.eligible_total = len(rows) if eligible_total is None else eligible_total
        self.calls: list[str] = []

    async def fetchval(self, sql: str, *params):
        self.calls.append(sql)
        return self.eligible_total

    async def fetch(self, sql: str, *params):
        self.calls.append(sql)
        limit = int(params[-1])
        if "(observed_at, id) >" in sql:
            cursor_id = int(params[-2])
            rows = [row for row in self.rows if int(row["id"]) > cursor_id]
        else:
            rows = self.rows
        return rows[:limit]


@pytest.mark.asyncio
async def test_batch_pages_every_row_and_accounts_for_every_skip() -> None:
    conn = BatchConnection([_row(index) for index in range(1, 702)])
    trusted, metrics = await load_complete_batch(
        conn,
        workspace_id="ws-a",
        group="global",
        model_version="bayesian_calibration.v1",
        source_type=None,
        source_id=None,
        operational_limit=1000,
        allow_manual=False,
    )
    assert trusted == []
    assert metrics == {
        "eligible_total": 701,
        "processed_total": 0,
        "skipped_total": 701,
        "skipped_by_reason": {"manual_ancestor": 701},
        "complete": False,
        "provenance_complete": False,
        "binary_evaluation_complete": False,
        "reason": "no_trusted_observations",
    }
    assert sum("ORDER BY observed_at ASC, id ASC" in call for call in conn.calls) == 2


@pytest.mark.asyncio
async def test_one_invalid_row_keeps_authoritative_recompute_incomplete(
    monkeypatch,
) -> None:
    conn = BatchConnection([_row(1), _row(2)])

    async def resolve(_conn, **kwargs):
        source_id = kwargs["payload"]["source_id"]
        if source_id == "fixture-2":
            raise HTTPException(409, "invalid evidence")
        return {
            "source_type": "prediction_outcome",
            "source_id": source_id,
            "actual_status": "hit",
            "calibration_group": "global",
        }

    monkeypatch.setattr(
        calibration_recompute_batch,
        "resolve_authoritative_observation",
        resolve,
    )
    trusted, metrics = await load_complete_batch(
        conn,
        workspace_id="ws-a",
        group="global",
        model_version="bayesian_calibration.v1",
        source_type=None,
        source_id=None,
        operational_limit=1000,
        allow_manual=False,
    )

    assert len(trusted) == 1
    assert metrics["processed_total"] == 1
    assert metrics["skipped_total"] == 1
    assert metrics["complete"] is False
    assert metrics["provenance_complete"] is False
    assert metrics["binary_evaluation_complete"] is False
    assert metrics["reason"] == "authoritative_recompute_incomplete"


class _ScopedConnection(BatchConnection):
    async def execute(self, sql: str, *params):
        self.calls.append(sql)

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, conn: _ScopedConnection) -> None:
        self.conn = conn

    def acquire(self):
        return self.conn


@pytest.mark.asyncio
@pytest.mark.parametrize("eligible_total", [5001, 10001])
async def test_operational_limit_aborts_before_state_upsert(
    monkeypatch, eligible_total: int
) -> None:
    rows = []
    if eligible_total == 5001:
        rows = [_row(index) for index in range(1, 5001)]
        rows.append(_row(5001, actual_status="miss"))
    conn = _ScopedConnection(rows, eligible_total=eligible_total)
    monkeypatch.setattr(
        calibration_service.auth,
        "pool",
        AsyncMock(return_value=_Pool(conn)),
    )
    with pytest.raises(HTTPException) as exc:
        await calibration_service.recompute(
            {"id": 1, "active_workspace_id": "ws-a"},
            {
                "calibration_group": "global",
                "limit": 5000 if eligible_total == 5001 else 10000,
            },
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["eligible_total"] == eligible_total
    assert exc.value.detail["complete"] is False
    assert not any("INSERT INTO calibration_states" in call for call in conn.calls)
    assert any("pg_advisory_xact_lock" in call for call in conn.calls)
