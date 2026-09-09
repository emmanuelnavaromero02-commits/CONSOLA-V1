from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration_service
from app.services.intelligence import persistence
from tests.pr552_calibration_live_helpers import (
    assert_calibration_writer_privileges,
    seed_real_signals,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.fixture(autouse=True)
def _enable_engine_under_test(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MATH_ENGINES_ENABLED", "true")


def _payload(outcome_id: str) -> dict:
    return {
        "source_type": "prediction_outcome",
        "source_id": outcome_id,
    }


@pytest.mark.asyncio
async def test_real_postgres_observation_is_atomic_idempotent_and_rls_scoped(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch,
) -> None:
    tenant, other_tenant, workspace = await seed_real_signals(
        postgres_with_real_init_schema
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    monkeypatch.setattr(
        calibration_service.auth,
        "pool",
        AsyncMock(return_value=pool),
    )
    user = {
        "role": "super_admin",
        "email": "operator@example.com",
        "active_tenant_id": tenant,
        "active_workspace_id": workspace,
    }
    try:
        monkeypatch.setattr(
            persistence.audit_service,
            "record_event",
            AsyncMock(),
        )
        recorded = await persistence.record_outcome(
            user,
            "signal-calibration",
            {
                "action_taken": "review",
                "actual_value": 12,
                "predicted_value": 999999,
                "outcome_summary": "Recorded through the normal endpoint flow",
            },
        )
        outcome = str(recorded["outcome"]["id"])
        assert recorded["outcome"]["evaluation_status"] == "hit"
        assert recorded["outcome"]["evaluation_rule_version"]
        assert recorded["outcome"]["evaluated_at"]
        assert recorded["outcome"]["evaluated_by"] == "omega_outcome_evaluator.v1"

        duplicate = await persistence.record_outcome(
            user,
            "signal-calibration",
            {
                "action_taken": "duplicate retry",
                "actual_value": 12,
                "outcome_summary": "Same realized outcome",
            },
        )
        assert duplicate["outcome"]["id"] == recorded["outcome"]["id"]
        reported = await persistence.record_outcome(
            user,
            "signal-calibration-unobserved",
            {
                "action_taken": "reported before maturity",
                "actual_value": 15,
                "learned_rule": "client-supplied rule must not publish",
            },
        )
        assert reported["outcome"]["prediction_error"] is None
        assert reported["outcome"]["learned_rule"] is None
        assert reported["outcome"]["evaluation_status"] is None
        assert reported["outcome"]["metadata"]["input_classification"] == (
            "reported_outcome"
        )

        admin = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            assert (
                await admin.fetchval(
                    "SELECT COUNT(*) FROM calibration_observations "
                    "WHERE workspace_id=$1 AND source_id=$2",
                    workspace,
                    outcome,
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT COUNT(*) FROM prediction_outcomes "
                    "WHERE workspace_id=$1 AND signal_id='signal-calibration'",
                    workspace,
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT COUNT(*) FROM control_room_lessons "
                    "WHERE workspace_id=$1 "
                    "AND item_id='signal-calibration-unobserved'",
                    workspace,
                )
                == 0
            )
        finally:
            await admin.close()
        left, right = await asyncio.gather(
            calibration_service.observe(user, _payload(outcome)),
            calibration_service.observe(user, _payload(outcome)),
        )
        retry = await calibration_service.observe(user, _payload(outcome))
        assert left == right == retry
        online_state = left["state"]
        assert online_state["metrics"]["complete"] is False
        assert online_state["metrics"]["provenance_complete"] is False
        assert online_state["metrics"]["skipped_total"] == 1
        assert online_state["metrics"]["reason"] == ("authoritative_recompute_required")
        for index in range(2, 6):
            extra = await persistence.record_outcome(
                user,
                f"signal-calibration-{index}",
                {
                    "action_taken": "review",
                    "actual_value": 12,
                    "outcome_summary": "Additional durable observed outcome",
                },
            )
            observed = await calibration_service.observe(
                user,
                _payload(str(extra["outcome"]["id"])),
            )
            assert observed["state"]["metrics"]["complete"] is False

        cross_tenant = {
            "active_tenant_id": other_tenant,
            "active_workspace_id": workspace,
        }
        with pytest.raises(HTTPException) as hidden:
            await calibration_service.observe(
                cross_tenant,
                _payload(outcome),
            )
        assert hidden.value.status_code == 404

        check = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            observation_count = await check.fetchval(
                "SELECT COUNT(*) FROM calibration_observations "
                "WHERE workspace_id=$1 AND source_id=$2",
                workspace,
                outcome,
            )
            state = await check.fetchrow(
                "SELECT sample_count, unknown_count FROM calibration_states "
                "WHERE workspace_id=$1",
                workspace,
            )
            await check.execute(
                "DELETE FROM calibration_states WHERE workspace_id=$1",
                workspace,
            )
        finally:
            await check.close()
        assert observation_count == 1
        assert dict(state) == {"sample_count": 5, "unknown_count": 0}
        await assert_calibration_writer_privileges(postgres_with_real_init_schema)

        recomputed = await calibration_service.recompute(
            user,
            {"calibration_group": online_state["calibration_group"]},
        )
        assert recomputed["observations_recomputed"] == 5
        assert recomputed["state"]["metrics"]["complete"] is True
        assert recomputed["state"]["metrics"]["provenance_complete"] is True
        assert recomputed["state"]["metrics"]["binary_evaluation_complete"] is True
        assert recomputed["state"]["sample_count"] == 5
        assert recomputed["state"]["prior"]["partial_pooling_applied"] is True

        async with pool.acquire() as scoped:
            async with scoped.transaction():
                await scoped.execute(
                    """
                    SELECT set_config('app.tenant_id', $1, true),
                           set_config('app.workspace_id', $2, true)
                    """,
                    tenant,
                    workspace,
                )
                server_state = await scoped.fetchrow(
                    "SELECT * FROM public.upsert_calibration_state($1::jsonb)",
                    json.dumps(
                        {
                            "calibration_group": online_state["calibration_group"],
                            "model_version": "bayesian_calibration.v1",
                            "posterior": {"alpha": 999, "beta": 1, "mean": 0.999},
                            "metrics": {
                                "complete": True,
                                "confidence_score": 1,
                            },
                        }
                    ),
                )
        check = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            parent_evidence = await check.fetchrow(
                """
                SELECT *
                  FROM public.authoritative_calibration_parent_evidence(
                      $1::uuid, $2::uuid, $3, 'bayesian_calibration.v1'
                  )
                """,
                workspace,
                tenant,
                online_state["calibration_group"],
            )
        finally:
            await check.close()
        assert parent_evidence["parent_group"].startswith("global:")
        assert parent_evidence["parent_sample_count"] == 5
        server_posterior = json.loads(server_state["posterior"])
        assert float(server_posterior["alpha"]) != 999
        assert float(server_state["confidence_score"]) != 1

        check = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            await check.execute(
                "UPDATE prediction_outcomes SET actual_value=13 WHERE id=$1",
                int(outcome),
            )
        finally:
            await check.close()
        with pytest.raises(HTTPException) as tampered:
            await calibration_service.observe(user, _payload(outcome))
        assert tampered.value.status_code == 409
        assert tampered.value.detail["reason"] == (
            "authoritative_evaluation_unavailable"
        )
    finally:
        await pool.close()
