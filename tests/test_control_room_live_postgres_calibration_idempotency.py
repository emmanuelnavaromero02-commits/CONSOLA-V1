from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration_service
from app.services.intelligence import persistence
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _seed(
    dsn: str,
) -> tuple[str, str, str]:
    conn = await asyncpg.connect(dsn)
    suffix = uuid.uuid4().hex[:12]
    try:
        tenant = await conn.fetchval(
            "INSERT INTO tenants(name, slug) VALUES($1, $2) RETURNING id",
            f"Calibration tenant {suffix}",
            f"calibration-{suffix}",
        )
        other_tenant = await conn.fetchval(
            "INSERT INTO tenants(name, slug) VALUES($1, $2) RETURNING id",
            f"Other tenant {suffix}",
            f"calibration-other-{suffix}",
        )
        workspace = await conn.fetchval(
            "INSERT INTO workspaces(tenant_id, name) VALUES($1, $2) RETURNING id",
            tenant,
            f"Calibration workspace {suffix}",
        )
        await conn.execute(
            """
            INSERT INTO intelligence_signals(
                signal_id, tenant_id, workspace_id, cartridge_id, dataset,
                domain, entity_kind, entity_id, entity_label, metric, period_key,
                actual_value, expected_value, predicted_value,
                prediction_horizon_days, signal_subtype, summary, metadata
            )
            VALUES(
                'signal-calibration', $1, $2, 'replicon', 'gold_margin',
                'Operacion', 'project', 'P-1', 'Project 1', 'margin', '2026-07',
                12, 10, 12, 30, 'future_opportunity', 'Predicted margin',
                '{"source_system":"replicon","source_dataset":"gold_margin",'
                '"evidence_pack_id":"evidence-real","input_classification":"observed",'
                '"observed":true,"evidence_refs":[{"source_system":"replicon",'
                '"source_dataset":"gold_margin","source_record_id":"P-1",'
                '"source_field":"margin","observed_value":12}]}'::jsonb
            )
            """,
            tenant,
            workspace,
        )
        privileges = await conn.fetchrow(
            """
            SELECT
              has_table_privilege(
                'omega_console', 'prediction_outcomes', 'SELECT'
              ) AS outcome_select,
              has_table_privilege(
                'omega_console', 'calibration_observations', 'SELECT'
              ) AS observation_select
            """
        )
        assert dict(privileges) == {
            "outcome_select": True,
            "observation_select": True,
        }
        return str(tenant), str(other_tenant), str(workspace)
    finally:
        await conn.close()


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
    tenant, other_tenant, workspace = await _seed(postgres_with_real_init_schema)
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
            assert await admin.fetchval(
                "SELECT COUNT(*) FROM prediction_outcomes "
                "WHERE workspace_id=$1 AND signal_id='signal-calibration'",
                workspace,
            ) == 1
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
        assert online_state["metrics"]["reason"] == (
            "authoritative_recompute_required"
        )

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
            update_privileges = await check.fetchrow(
                """
                SELECT
                  has_table_privilege(
                    'omega_console', 'calibration_observations', 'UPDATE'
                  ) AS observation_update,
                  has_table_privilege(
                    'omega_console', 'prediction_outcomes', 'UPDATE'
                  ) AS outcome_update
                """
            )
            await check.execute(
                "DELETE FROM calibration_states WHERE workspace_id=$1",
                workspace,
            )
        finally:
            await check.close()
        assert observation_count == 1
        assert dict(state) == {"sample_count": 1, "unknown_count": 0}
        assert dict(update_privileges) == {
            "observation_update": False,
            "outcome_update": False,
        }

        recomputed = await calibration_service.recompute(
            user,
            {"calibration_group": online_state["calibration_group"]},
        )
        assert recomputed["observations_recomputed"] == 1
        assert recomputed["state"]["metrics"]["complete"] is True
        assert recomputed["state"]["metrics"]["provenance_complete"] is True
        assert recomputed["state"]["metrics"]["binary_evaluation_complete"] is True
        assert recomputed["state"]["sample_count"] == 1

        check = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            await check.execute(
                "UPDATE prediction_outcomes SET actual_value=13 WHERE id=$1",
                int(outcome),
            )
        finally:
            await check.close()
        repeated = await calibration_service.observe(user, _payload(outcome))
        assert repeated["state"]["sample_count"] == 1
    finally:
        await pool.close()
