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
                12, 10, 10, 30, 'observed', 'Observed margin',
                '{"source_system":"replicon","source_dataset":"gold_margin",'
                '"evidence_pack_id":"evidence-real","observed":true}'::jsonb
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


def _payload(outcome_id: str, actual_value: float = 12) -> dict:
    return {
        "source_type": "prediction_outcome",
        "source_id": outcome_id,
        "predicted_metric": "margin",
        "predicted_value": 10,
        "actual_value": actual_value,
        "actual_status": "unknown",
        "horizon_days": 30,
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
        left, right = await asyncio.gather(
            calibration_service.observe(user, _payload(outcome)),
            calibration_service.observe(user, _payload(outcome)),
        )
        retry = await calibration_service.observe(user, _payload(outcome))
        assert left == right == retry

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
                "UPDATE prediction_outcomes SET actual_value=13 WHERE id=$1",
                int(outcome),
            )
        finally:
            await check.close()
        assert observation_count == 1
        assert dict(state) == {"sample_count": 1, "unknown_count": 1}
        assert dict(update_privileges) == {
            "observation_update": False,
            "outcome_update": False,
        }

        with pytest.raises(HTTPException) as changed:
            await calibration_service.observe(
                user,
                _payload(outcome, actual_value=13),
            )
        assert (changed.value.status_code, changed.value.detail) == (
            409,
            "calibration evidence conflict",
        )
    finally:
        await pool.close()
