from __future__ import annotations

import pytest

from app.services.intelligence import decision_orchestrator
from app.services.intelligence.source_provenance import resolve_source_provenance
from tests.decision_orchestrator_harness import (
    FakeOrchestratorDB,
    _user,
    runtime_evidence,
)


class GraphConnection:
    def __init__(self, graph: dict[tuple[str, str], tuple[str, str]]) -> None:
        self.graph = graph

    async def fetchrow(self, sql: str, workspace_id: str, source_id: str):
        if "FROM monte_carlo_simulations" in sql:
            parent = self.graph.get(("monte_carlo_simulation", source_id))
            return (
                {"source_type": parent[0], "source_id": parent[1]} if parent else None
            )
        if "FROM calibration_observations" in sql:
            parent = self.graph.get(("calibration_observation", source_id))
            return (
                {"source_type": parent[0], "source_id": parent[1]} if parent else None
            )
        if "FROM decision_options" in sql:
            parent = self.graph.get(("decision_option", source_id))
            return {"signal_id": parent[1]} if parent else None
        if "FROM prediction_outcomes" in sql:
            parent = self.graph.get(("prediction_outcome", source_id))
            return {"signal_id": parent[1], "metadata": {}} if parent else None
        if "FROM intelligence_signals" in sql:
            if ("signal", source_id) not in self.graph:
                return None
            return {
                "signal_subtype": "observed",
                "source_system": "control_room",
                "source_dataset": "metric_observations",
                "evidence_pack_id": "evidence-real",
                "metadata": {"observed": True},
            }
        if "FROM backtest_results" in sql:
            if ("backtest_case", source_id) not in self.graph:
                return None
            return {
                "label_source": "historical_outcome",
                "run_mode": "historical_replay",
                "status": "ok",
                "completed_at": "2026-01-01T00:00:00Z",
                "source_system": "replicon",
                "source_dataset": "observed_outcomes",
            }
        raise AssertionError(sql)

    async def fetchval(self, sql: str, *params):
        return 1


@pytest.mark.asyncio
async def test_manual_fixture_is_rejected_through_multiple_ancestors() -> None:
    conn = GraphConnection(
        {
            ("monte_carlo_simulation", "mc-outer"): (
                "calibration_observation",
                "obs-inner",
            ),
            ("calibration_observation", "obs-inner"): (
                "monte_carlo_simulation",
                "mc-manual",
            ),
            ("monte_carlo_simulation", "mc-manual"): (
                "manual_fixture",
                "fixture-a",
            ),
        }
    )
    result = await resolve_source_provenance(
        conn,
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="mc-outer",
    )
    assert result.trusted is False
    assert result.reason == "manual_ancestor"
    assert result.depth == 3


@pytest.mark.asyncio
async def test_cycle_and_missing_lineage_fail_closed() -> None:
    cycle = GraphConnection(
        {
            ("monte_carlo_simulation", "mc-a"): (
                "calibration_observation",
                "obs-a",
            ),
            ("calibration_observation", "obs-a"): (
                "monte_carlo_simulation",
                "mc-a",
            ),
        }
    )
    cycled = await resolve_source_provenance(
        cycle,
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="mc-a",
    )
    missing = await resolve_source_provenance(
        GraphConnection({}),
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="missing",
    )
    assert (cycled.trusted, cycled.reason) == (False, "provenance_cycle")
    assert (missing.trusted, missing.reason) == (False, "provenance_incomplete")


@pytest.mark.asyncio
async def test_option_outcome_and_signal_lineage_is_observed() -> None:
    conn = GraphConnection(
        {
            ("decision_option", "option-a"): ("signal", "signal-a"),
            ("prediction_outcome", "outcome-a"): ("signal", "signal-a"),
            ("signal", "signal-a"): ("observed", "terminal"),
        }
    )
    for source_type, source_id in (
        ("decision_option", "option-a"),
        ("prediction_outcome", "outcome-a"),
    ):
        result = await resolve_source_provenance(
            conn,
            workspace_id="ws-a",
            source_type=source_type,
            source_id=source_id,
        )
        assert result.trusted is True
        assert result.reason == "observed_signal"


@pytest.mark.asyncio
async def test_backtest_provenance_requires_completed_observed_run() -> None:
    trusted = await resolve_source_provenance(
        GraphConnection({("backtest_case", "case-a"): ("observed", "terminal")}),
        workspace_id="ws-a",
        source_type="backtest_case",
        source_id="case-a",
    )
    missing = await resolve_source_provenance(
        GraphConnection({}),
        workspace_id="ws-a",
        source_type="backtest_case",
        source_id="missing",
    )
    assert (trusted.trusted, trusted.reason) == (True, "observed_backtest")
    assert (missing.trusted, missing.reason) == (
        False,
        "backtest_provenance_incomplete",
    )


@pytest.mark.asyncio
async def test_provenance_depth_limit_fails_closed() -> None:
    graph = {}
    for index in range(14):
        source_type = (
            "monte_carlo_simulation" if index % 2 == 0 else "calibration_observation"
        )
        next_type = (
            "calibration_observation" if index % 2 == 0 else "monte_carlo_simulation"
        )
        graph[(source_type, f"node-{index}")] = (next_type, f"node-{index + 1}")
    result = await resolve_source_provenance(
        GraphConnection(graph),
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="node-0",
    )
    assert (result.trusted, result.reason) == (False, "provenance_depth_exceeded")


class OrchestratorManualAncestor:
    async def fetchrow(self, sql: str, *params):
        if "simulation_id AS source_id" in sql:
            return {
                "tenant_id": None,
                "workspace_id": "ws-a",
                "source_id": "mc-a",
                "simulation_source_type": "manual_fixture",
                "simulation_source_id": "fixture-a",
                "distribution_summary": {},
                "sensitivity": [],
                "evidence_refs": [],
                "assumptions": {},
                "option_comparison": {},
            }
        if "SELECT source_type, source_id" in sql:
            return {"source_type": "manual_fixture", "source_id": "fixture-a"}
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_orchestrator_rejects_transitive_manual_fixture_in_production(
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(decision_orchestrator.DecisionOrchestratorError) as exc:
        await decision_orchestrator._load_source(
            OrchestratorManualAncestor(),
            tenant_id=None,
            workspace_id="ws-a",
            source_type="monte_carlo_simulation",
            source_id="mc-a",
            payload={},
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "source_provenance_untrusted"


@pytest.mark.asyncio
async def test_orchestrator_rejects_unknown_wisdom_bit_alias() -> None:
    with pytest.raises(decision_orchestrator.DecisionOrchestratorError) as exc:
        await decision_orchestrator._load_source(
            GraphConnection({}),
            tenant_id=None,
            workspace_id="ws-a",
            source_type="wisdom_bit",
            source_id="manual-payload-alias",
            payload={"metrics": {"invented": 1}},
        )
    assert (exc.value.status_code, exc.value.detail) == (
        409,
        "source_provenance_untrusted",
    )


@pytest.mark.asyncio
async def test_control_room_source_is_revalidated_from_server_evidence() -> None:
    user = _user(7)
    db = FakeOrchestratorDB()
    db.current_tenant_id = user["active_tenant_id"]
    db.current_workspace_id = user["active_workspace_id"]
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="observed-source",
        item_kind="anomaly",
        source_dataset="gold_metrics",
        metadata={
            "data_status": "ready",
            "observed_at": "2026-07-10T00:00:00Z",
            "metric_type": "scalar",
            "observed_value": 1,
            **runtime_evidence(user, "observed-source"),
        },
    )
    result = await resolve_source_provenance(
        db,
        workspace_id=user["active_workspace_id"],
        source_type="control_room_item",
        source_id="observed-source",
    )
    assert (result.trusted, result.reason) == (
        True,
        "observed_control_room_item",
    )
