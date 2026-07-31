from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.routers.intelligence import OutcomeRequest
from app.services.intelligence import calibration
from app.services.intelligence.calibration_authoritative_evidence import (
    resolve_authoritative_observation,
)
from app.services.intelligence.calibration_recompute_batch import load_complete_batch


WORKSPACE = "22222222-2222-2222-2222-222222222222"
TENANT = "11111111-1111-1111-1111-111111111111"
GROUP = "source_type:replicon:margin:v1"


def _authoritative_row(*, evaluated: bool = True) -> dict:
    return {
        "outcome_id": "41",
        "outcome_tenant_id": TENANT,
        "signal_id": "signal-a",
        "option_id": None,
        "option_matches": True,
        "action_taken": "review",
        "actual_value": 12,
        "outcome_created_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
        "evaluation_status": "hit" if evaluated else None,
        "evaluation_rule_version": "margin-evaluation.v1" if evaluated else None,
        "evaluated_at": (
            datetime(2026, 7, 31, tzinfo=timezone.utc) if evaluated else None
        ),
        "evaluated_by": "evaluation-engine" if evaluated else None,
        "metric": "margin",
        "signal_predicted_value": 10,
        "signal_subtype": "observed",
        "source_system": "replicon",
        "source_dataset": "gold_margin",
        "evidence_pack_id": "evidence-real",
        "prediction_horizon_days": 45,
        "signal_metadata": {"observed": True},
    }


class _ResolverConnection:
    def __init__(self, *, evaluated: bool = True) -> None:
        self.evaluated = evaluated
        self.queries: list[str] = []

    async def fetchrow(self, sql: str, *params):
        self.queries.append(sql)
        if "FROM prediction_outcomes outcome" in sql:
            assert params == (WORKSPACE, "41", TENANT)
            return _authoritative_row(evaluated=self.evaluated)
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_authority_uses_durable_horizon_and_versioned_binary_evaluation() -> None:
    conn = _ResolverConnection()
    resolved = await resolve_authoritative_observation(
        conn,
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        payload={"source_type": "prediction_outcome", "source_id": "41"},
        allow_manual=False,
    )
    assert resolved["horizon_days"] == 45
    assert resolved["actual_status"] == "hit"
    assert resolved["evaluation_rule_version"] == "margin-evaluation.v1"
    query = conn.queries[0]
    assert "signal.prediction_horizon_days AS prediction_horizon_days" in query
    assert "outcome.evaluation_status" in query


class _BatchConnection(_ResolverConnection):
    def __init__(self) -> None:
        super().__init__()
        self.legacy = {
            "id": 7,
            "observed_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
            "source_type": "prediction_outcome",
            "source_id": "41",
            "actual_status": "miss",
            "predicted_metric": "forged",
            "predicted_value": 999999,
            "actual_value": -999999,
            "calibration_group": GROUP,
            "authoritative_calibration_group": GROUP,
            "model_version": calibration.MODEL_VERSION,
            "provenance_status": "verified",
            "evidence_refs": [],
        }

    async def fetchval(self, sql: str, *_params):
        self.queries.append(sql)
        if "COUNT(*) FROM calibration_observations" in sql:
            return 1
        raise AssertionError(sql)

    async def fetch(self, sql: str, *_params):
        self.queries.append(sql)
        if "FROM calibration_observations" in sql:
            return [self.legacy]
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_recompute_rebuilds_same_observation_instead_of_legacy_claims() -> None:
    conn = _BatchConnection()
    rows, metrics = await load_complete_batch(
        conn,
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        group=GROUP,
        model_version=calibration.MODEL_VERSION,
        source_type=None,
        source_id=None,
        operational_limit=10,
        allow_manual=False,
    )
    assert len(rows) == 1
    rebuilt = rows[0]
    assert rebuilt["predicted_metric"] == "margin"
    assert rebuilt["predicted_value"] == 10
    assert rebuilt["actual_value"] == 12
    assert rebuilt["actual_status"] == "hit"
    assert rebuilt["horizon_days"] == 45
    assert metrics["processed_total"] == 1
    state = calibration.recompute_state(
        rows,
        calibration_group=GROUP,
        model_version=calibration.MODEL_VERSION,
    )
    assert state["posterior"]["alpha"] == 2
    assert state["posterior"]["beta"] == 1
    assert state["metrics"]["sample_count"] == 1


def test_unknown_outcomes_never_count_as_calibratable_samples() -> None:
    observations = [
        {
            "actual_status": "unknown",
            "predicted_metric": "margin",
            "predicted_value": 10,
            "actual_value": 12,
        }
        for _ in range(10)
    ]
    state = calibration.recompute_state(
        observations,
        calibration_group=GROUP,
        model_version=calibration.MODEL_VERSION,
    )
    assert state["posterior"]["alpha"] == 1
    assert state["posterior"]["beta"] == 1
    assert state["metrics"]["sample_count"] == 0
    assert state["metrics"]["confidence_score"] == 0


@pytest.mark.parametrize(
    "field,value",
    (
        ("evaluation_status", "hit"),
        ("evaluation_rule_version", "client-rule.v1"),
        ("predicted_probability", 0.99),
        ("tolerance", 1000),
    ),
)
def test_outcome_client_cannot_choose_binary_evaluation(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        OutcomeRequest(
            action_taken="review",
            actual_value=12,
            **{field: value},
        )
