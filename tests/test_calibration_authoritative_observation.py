from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration
from app.services.intelligence.calibration_authoritative_evidence import (
    resolve_authoritative_observation,
)
from app.services.intelligence.calibration_state_repository import (
    _observation_identity,
)
from app.services.intelligence.calibration_source_validation import (
    calibration_source_exists,
)


OUTCOME_ID = "41"
WORKSPACE = "22222222-2222-2222-2222-222222222222"


def _client_payload(**overrides) -> dict:
    payload = {
        "source_type": "prediction_outcome",
        "source_id": OUTCOME_ID,
        "predicted_metric": "margin",
        "predicted_value": 10,
        "actual_value": 12,
        "actual_status": "hit",
    }
    payload.update(overrides)
    return payload


class _Connection:
    def __init__(
        self,
        *,
        workspace: str = WORKSPACE,
        tenant: str | None = None,
        outcome: bool = True,
    ) -> None:
        self.workspace = workspace
        self.tenant = tenant
        self.outcome = outcome
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *params):
        self.calls.append((sql, params))
        if "FROM prediction_outcomes" not in sql or not self.outcome:
            return None
        if params != (self.workspace, OUTCOME_ID, self.tenant):
            return None
        return {
            "outcome_id": OUTCOME_ID,
            "signal_id": "signal-a",
            "option_id": "option-a",
            "option_matches": True,
            "action_taken": "review",
            "outcome_predicted_value": 999999,
            "actual_value": 12,
            "outcome_metadata": {"reported_by": "operator@example.com"},
            "outcome_created_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
            "evaluation_status": "hit",
            "evaluation_rule_version": "margin-evaluation.v1",
            "evaluated_at": datetime(2026, 7, 31, tzinfo=timezone.utc),
            "evaluated_by": "evaluation-engine",
            "metric": "margin",
            "signal_predicted_value": 10,
            "signal_subtype": "observed",
            "source_system": "replicon",
            "source_dataset": "gold_margin",
            "evidence_pack_id": "evidence-real",
            "prediction_horizon_days": 30,
            "signal_metadata": {"observed": True},
        }


@pytest.mark.asyncio
async def test_decision_option_without_authoritative_outcome_fails_closed() -> None:
    conn = _Connection()
    with pytest.raises(HTTPException) as exc:
        await resolve_authoritative_observation(
            conn,
            workspace_id=WORKSPACE,
            payload=_client_payload(
                source_type="decision_option",
                source_id="option-a",
            ),
            allow_manual=False,
        )
    assert exc.value.status_code == 404
    assert conn.calls == []


@pytest.mark.asyncio
async def test_recompute_never_treats_decision_option_as_observed_truth() -> None:
    conn = _Connection()
    assert not await calibration_source_exists(
        conn,
        workspace_id=WORKSPACE,
        source_type="decision_option",
        source_id="option-a",
    )
    assert conn.calls == []


@pytest.mark.asyncio
async def test_recompute_resolves_normal_outcome_server_side() -> None:
    conn = _Connection()
    assert await calibration_source_exists(
        conn,
        workspace_id=WORKSPACE,
        source_type="prediction_outcome",
        source_id=OUTCOME_ID,
    )
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_normal_durable_outcome_resolves_server_owned_values() -> None:
    resolved = await resolve_authoritative_observation(
        _Connection(),
        workspace_id=WORKSPACE,
        payload=_client_payload(),
        allow_manual=False,
    )
    assert resolved["source_type"] == "prediction_outcome"
    assert resolved["source_id"] == OUTCOME_ID
    assert resolved["predicted_metric"] == "margin"
    assert resolved["predicted_value"] == 10
    assert resolved["actual_value"] == 12
    assert resolved["actual_status"] == "hit"
    assert resolved["evaluation_rule_version"] == "margin-evaluation.v1"
    assert resolved["model_version"] == calibration.MODEL_VERSION
    assert resolved["input_classification"] == "observed"


@pytest.mark.asyncio
async def test_forged_client_claim_is_rejected_before_calibration() -> None:
    with pytest.raises(HTTPException) as exc:
        await resolve_authoritative_observation(
            _Connection(),
            workspace_id=WORKSPACE,
            payload=_client_payload(actual_status="miss", actual_value=-999999),
            allow_manual=False,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "calibration claims do not match authoritative outcome"


@pytest.mark.asyncio
async def test_outcome_without_server_evaluation_is_explicitly_insufficient() -> None:
    conn = _Connection()
    original = conn.fetchrow

    async def fetchrow(sql: str, *params):
        row = await original(sql, *params)
        return {
            **row,
            "evaluation_status": None,
            "evaluation_rule_version": None,
            "evaluated_at": None,
            "evaluated_by": None,
        }

    conn.fetchrow = fetchrow
    with pytest.raises(HTTPException) as exc:
        await resolve_authoritative_observation(
            conn,
            workspace_id=WORKSPACE,
            payload=_client_payload(),
            allow_manual=False,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "status": "insufficient_data",
        "reason": "authoritative_evaluation_unavailable",
        "complete": False,
        "calibratable_sample_count": 0,
    }


@pytest.mark.asyncio
async def test_cross_workspace_outcome_is_not_visible() -> None:
    with pytest.raises(HTTPException) as exc:
        await resolve_authoritative_observation(
            _Connection(workspace="other-workspace"),
            workspace_id=WORKSPACE,
            payload=_client_payload(),
            allow_manual=False,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_outcome_is_not_visible() -> None:
    with pytest.raises(HTTPException) as exc:
        await resolve_authoritative_observation(
            _Connection(tenant="tenant-b"),
            workspace_id=WORKSPACE,
            tenant_id="tenant-a",
            payload=_client_payload(),
            allow_manual=False,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_identity_is_stable_and_digest_changes_with_evidence() -> None:
    resolved = await resolve_authoritative_observation(
        _Connection(),
        workspace_id=WORKSPACE,
        payload=_client_payload(),
        allow_manual=False,
    )
    first = _observation_identity(workspace_id=WORKSPACE, payload=resolved)
    second = _observation_identity(
        workspace_id=WORKSPACE,
        payload={**resolved, "actual_value": 13},
    )
    assert first.observation_id == second.observation_id
    assert first.idempotency_key == second.idempotency_key
    assert first.evidence_digest != second.evidence_digest
    assert "posterior" not in first.identity_payload
