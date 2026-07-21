from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_projection import filter_business_items


USER = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


def _item(item_id: str, kind: str, **overrides) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-20",
        "metric_type": "count",
        "count": 1,
        "evidence_refs": [f"evidence:{item_id}"],
        **overrides,
    }


@pytest.mark.asyncio
async def test_agentops_uses_canonical_business_ids_for_executive_counters():
    items = [
        _item("alert-good", "agent_alert"),
        _item("signal-good", "intelligence_signal"),
        _item("diagnostic", "source_state", data_status="missing"),
        _item(
            "alert-derived-diagnostic",
            "agent_alert",
            parent_item_id="diagnostic",
        ),
    ]
    snapshot = AsyncMock(return_value={"raw": True})
    forbidden_dashboard = AsyncMock(
        side_effect=AssertionError("agent polling must not fetch datasets")
    )

    async def scoped(_pool, _user, work):
        return await work(object(), "tenant-A", "workspace-A")

    with (
        patch.object(
            control_room_service,
            "persisted_business_projection",
            new=AsyncMock(return_value=filter_business_items(items)),
        ),
        patch.object(control_room_service, "dashboard", new=forbidden_dashboard),
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(control_room_service, "_agentops_load_snapshot", new=snapshot),
        patch.object(
            control_room_service,
            "_agentops_payload_from_raw",
            side_effect=lambda raw, **_kwargs: raw,
        ),
    ):
        result = await control_room_service.agents_ops(USER)

    assert result == {"raw": True}
    forbidden_dashboard.assert_not_awaited()
    assert snapshot.await_args.kwargs["eligible_source_ids"] == {
        "control_room_item": [],
        "agent_alert": ["alert-good"],
        "intelligence_signal": ["signal-good"],
    }
    assert snapshot.await_args.kwargs["eligible_alert_ids"] == ["alert-good"]


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((" ".join(sql.split()), args))
        return []


class MixedOrchestrationConnection:
    runs = [
        ("run-valid", "control_room_item", "item-valid"),
        ("run-technical", "intelligence_signal", "item-technical"),
        ("run-manual", "manual", "manual-without-lineage"),
        ("run-unknown", "custom_source", "unknown-without-lineage"),
        ("run-linked-unknown", "custom_source", "item-valid"),
    ]

    async def fetch(self, sql: str, *args):
        assert "source_id = ANY($2::text[])" in sql
        assert "source_type NOT IN" not in sql
        eligible_by_type = {
            "control_room_item": set(args[1]),
            "agent_alert": set(args[2]),
            "intelligence_signal": set(args[3]),
        }
        linked_runs = {
            run_id
            for run_id, source_type, source_id in self.runs
            if source_id in eligible_by_type.get(source_type, set())
        }
        if "JOIN decision_orchestration_runs" not in sql:
            return [{"total": len(linked_runs), "latest_at": None}]
        executions = [
            ("run-valid", "monte_carlo", "completed"),
            ("run-technical", "monte_carlo", "completed"),
            ("run-manual", "rules", "completed"),
            ("run-unknown", "rules", "failed"),
            ("run-linked-unknown", "rules", "completed"),
        ]
        counts: dict[tuple[str, str], int] = {}
        for run_id, engine, status in executions:
            if run_id in linked_runs:
                counts[(engine, status)] = counts.get((engine, status), 0) + 1
        return [
            {
                "engine_name": engine,
                "execution_status": status,
                "total": total,
                "latest_at": None,
            }
            for (engine, status), total in sorted(counts.items())
        ]


class MixedSimulationConnection:
    async def fetch(self, sql: str, *args):
        assert "simulation.source_id = ANY($2::text[])" in sql
        assert "option.signal_id = ANY($2::text[])" in sql
        eligible_ids = set(args[1])
        option_signals = {
            "option-good": "signal-good",
            "option-technical": "signal-technical",
        }
        simulations = [
            ("signal", "signal-good"),
            ("signal", "signal-technical"),
            ("decision_option", "option-good"),
            ("decision_option", "option-technical"),
            ("wisdom_bit", "WB-TALENTO"),
            ("manual_fixture", "fixture-without-lineage"),
        ]
        counts: dict[str, int] = {}
        for source_type, source_id in simulations:
            direct = source_type == "signal" and source_id in eligible_ids
            via_option = (
                source_type == "decision_option"
                and option_signals.get(source_id) in eligible_ids
            )
            if direct or via_option:
                counts[source_type] = counts.get(source_type, 0) + 1
        return [
            {"source_type": source_type, "total": total, "latest_at": None}
            for source_type, total in sorted(counts.items())
        ]


@pytest.mark.asyncio
async def test_agent_alert_orchestration_and_execution_queries_are_id_bounded():
    conn = RecordingConnection()

    await control_room_service._agentops_alert_rows(
        conn,
        workspace_id="workspace-A",
        allowed_param=None,
        eligible_alert_ids=["alert-good"],
    )
    await control_room_service._agentops_orchestration_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"decision_orchestration_runs": True},
        eligible_source_ids={
            "control_room_item": [],
            "agent_alert": ["alert-good"],
            "intelligence_signal": ["signal-good"],
        },
    )
    await control_room_service._agentops_execution_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"decision_orchestration_executions": True},
        eligible_source_ids={
            "control_room_item": [],
            "agent_alert": ["alert-good"],
            "intelligence_signal": ["signal-good"],
        },
    )

    assert "item_id = ANY($3::text[])" in conn.calls[0][0]
    assert conn.calls[0][1][2] == ["alert-good"]
    assert "source_id = ANY($2::text[])" in conn.calls[1][0]
    assert conn.calls[1][1][1:] == ([], ["alert-good"], ["signal-good"])
    assert "JOIN decision_orchestration_runs" in conn.calls[2][0]
    assert "run.source_id = ANY($2::text[])" in conn.calls[2][0]
    assert "source_type NOT IN" not in conn.calls[1][0]
    assert "source_type NOT IN" not in conn.calls[2][0]


@pytest.mark.asyncio
async def test_agentops_mixed_orchestrations_require_demonstrable_business_lineage():
    conn = MixedOrchestrationConnection()
    eligible_source_ids = {
        "control_room_item": ["item-valid"],
        "agent_alert": [],
        "intelligence_signal": [],
    }

    runs = await control_room_service._agentops_orchestration_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"decision_orchestration_runs": True},
        eligible_source_ids=eligible_source_ids,
    )
    executions = await control_room_service._agentops_execution_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"decision_orchestration_executions": True},
        eligible_source_ids=eligible_source_ids,
    )

    assert runs == [{"total": 1, "latest_at": None}]
    assert sum(row["total"] for row in executions) == 1
    assert {row["engine_name"] for row in executions} == {"monte_carlo"}


@pytest.mark.asyncio
async def test_agentops_monte_carlo_counts_only_demonstrable_business_lineage():
    conn = RecordingConnection()

    await control_room_service._agentops_monte_carlo_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"monte_carlo_simulations": True},
        eligible_item_ids=["signal-good"],
    )

    sql, args = conn.calls[0]
    assert "simulation.source_type = 'signal'" in sql
    assert "simulation.source_id = ANY($2::text[])" in sql
    assert "simulation.source_type = 'decision_option'" in sql
    assert "FROM decision_options option" in sql
    assert "option.signal_id = ANY($2::text[])" in sql
    assert "manual_fixture" not in sql
    assert "backtest_case" not in sql
    assert "wisdom_bit" not in sql
    assert args == ("workspace-A", ["signal-good"])


@pytest.mark.asyncio
async def test_agentops_mixed_simulations_count_only_eligible_business_sources():
    rows = await control_room_service._agentops_monte_carlo_rows(
        MixedSimulationConnection(),
        workspace_id="workspace-A",
        table_exists={"monte_carlo_simulations": True},
        eligible_item_ids=["signal-good"],
    )

    assert sum(row["total"] for row in rows) == 2
    assert {row["source_type"] for row in rows} == {"signal", "decision_option"}


def test_agentops_global_calibration_is_operational_diagnostic_not_business_count():
    now = datetime(2026, 7, 17, tzinfo=UTC)
    raw = {
        "agents": [],
        "runs": [],
        "alert_rows": [],
        "origin_rows": [],
        "monte_carlo_rows": [{"source_type": "signal", "total": 2, "latest_at": now}],
        "operational_calibration_rows": [
            {"total": 3, "sample_count": 21, "latest_at": now}
        ],
        "orchestration_rows": [],
        "execution_rows": [],
    }

    payload = control_room_service._agentops_payload_from_raw(
        raw,
        tenant_id="tenant-A",
        workspace_id="workspace-A",
    )

    assert payload["summary"]["monte_carlo_simulations"] == 2
    assert payload["summary"]["bayesian_calibration_states"] == 0
    assert payload["summary"]["bayesian_calibration_samples"] == 0
    calibration = next(
        engine
        for engine in payload["engines"]
        if engine["engine"] == "bayesian_calibration"
    )
    assert calibration["evidence_count"] == 0
    assert calibration["sample_count"] == 0
    assert payload["operational_diagnostics"] == [
        {
            "diagnostic": "bayesian_calibration_global",
            "scope": "workspace",
            "state_count": 3,
            "sample_count": 21,
            "latest_at": now.isoformat(),
            "included_in_business_counters": False,
        }
    ]
