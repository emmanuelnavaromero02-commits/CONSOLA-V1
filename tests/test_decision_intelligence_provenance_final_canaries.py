from __future__ import annotations

import pytest

from app.services.intelligence.source_provenance import resolve_source_provenance


class ProvenanceConnection:
    def __init__(self, *, backtest: dict | None = None) -> None:
        self.backtest = backtest

    async def fetchrow(self, sql: str, _workspace_id: str, source_id: str):
        if "FROM monte_carlo_simulations" in sql:
            return {"source_type": "signal", "source_id": "signal-observed"}
        if "FROM prediction_outcomes" in sql:
            return {
                "signal_id": "signal-observed",
                "option_id": "option-a",
                "action_taken": "review",
                "actual_value": 1,
                "outcome_summary": "manual scenario",
                "metadata": {
                    "source_type": "prediction_outcome",
                    "source_system": "manual_fixture",
                    "source_dataset": "synthetic_outcome",
                    "input_classification": "scenario_assumption",
                    "observed": False,
                    "evidence_refs": [],
                },
            }
        if "FROM intelligence_signals" in sql:
            return {
                "signal_subtype": "observed",
                "source_system": "replicon",
                "source_dataset": "gold_observations",
                "evidence_pack_id": "evidence-real",
                "metadata": {"observed": True},
            }
        if "FROM backtest_results" in sql:
            return self.backtest
        raise AssertionError(sql)

    async def fetchval(self, _sql: str, *_params):
        return "backtest_results"


@pytest.mark.asyncio
async def test_monte_carlo_scenario_is_never_observed_calibration_evidence() -> None:
    result = await resolve_source_provenance(
        ProvenanceConnection(),
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="mc-scenario",
    )
    assert (result.trusted, result.reason) == (False, "scenario_assumption")


@pytest.mark.asyncio
async def test_prediction_outcome_requires_server_owned_observed_evidence() -> None:
    result = await resolve_source_provenance(
        ProvenanceConnection(),
        workspace_id="ws-a",
        source_type="prediction_outcome",
        source_id="outcome-manual",
    )
    assert result.trusted is False
    assert result.reason == "prediction_outcome_provenance_incomplete"


def _backtest(**overrides) -> dict:
    row = {
        "label_source": "historical_rule",
        "actual_label": True,
        "result": {"label_rule": "durable_historical_rule"},
        "run_mode": "historical_replay",
        "status": "ok",
        "completed_at": "2026-01-01T00:00:00Z",
        "source_system": "replicon",
        "source_dataset": "observed_outcomes",
        "labels_available": 10,
        "labels_required": 10,
        "insufficient_labeled_data": False,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_outcome_linked_backtest_follows_manual_outcome_transitively() -> None:
    result = await resolve_source_provenance(
        ProvenanceConnection(
            backtest=_backtest(
                label_source="outcome",
                run_mode="outcome_linked",
                result={"outcome_id": "outcome-manual"},
            )
        ),
        workspace_id="ws-a",
        source_type="backtest_case",
        source_id="case-outcome",
    )
    assert result.trusted is False
    assert result.reason == "prediction_outcome_provenance_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"label_source": "manual"},
        {"label_source": "mock"},
        {"label_source": "synthetic"},
        {"label_source": "fixture_v2"},
        {"label_source": "unknown"},
        {"status": "insufficient_labeled_data"},
        {"labels_available": 9},
        {"actual_label": None},
    ],
)
async def test_backtest_requires_successful_sufficient_observed_labels(
    overrides,
) -> None:
    result = await resolve_source_provenance(
        ProvenanceConnection(backtest=_backtest(**overrides)),
        workspace_id="ws-a",
        source_type="backtest_case",
        source_id="case-a",
    )
    assert result.trusted is False
    assert result.reason == "backtest_provenance_incomplete"


@pytest.mark.asyncio
async def test_backtest_with_sufficient_observed_labels_is_trusted() -> None:
    result = await resolve_source_provenance(
        ProvenanceConnection(backtest=_backtest()),
        workspace_id="ws-a",
        source_type="backtest_case",
        source_id="case-a",
    )
    assert (result.trusted, result.reason) == (True, "observed_backtest")
