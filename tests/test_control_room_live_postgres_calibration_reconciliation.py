from __future__ import annotations

from pathlib import Path
import uuid

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/init/99zze_calibration_authoritative_reconciliation.sql"
GROUP = "source_type:replicon:margin:v1"


@pytest.mark.asyncio
async def test_real_upgrade_quarantines_fabricated_and_reconciles_once(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    transaction = conn.transaction()
    await transaction.start()
    suffix = uuid.uuid4().hex[:12]
    try:
        tenant = await conn.fetchval(
            "INSERT INTO tenants(name, slug) VALUES($1, $2) RETURNING id",
            f"Legacy tenant {suffix}",
            f"legacy-{suffix}",
        )
        workspace = await conn.fetchval(
            "INSERT INTO workspaces(tenant_id, name) VALUES($1, $2) RETURNING id",
            tenant,
            f"Legacy workspace {suffix}",
        )
        await conn.execute(
            """
            INSERT INTO intelligence_signals(
                signal_id, tenant_id, workspace_id, cartridge_id, dataset,
                domain, entity_kind, entity_id, entity_label, metric, period_key,
                predicted_value, prediction_horizon_days, signal_subtype,
                summary, metadata
            ) VALUES(
                $1, $2, $3, 'replicon', 'gold_margin', 'Operacion',
                'project', 'P-1', 'Project 1', 'margin', '2026-07',
                10, 45, 'observed', 'Observed margin',
                '{"source_system":"replicon","source_dataset":"gold_margin",'
                '"evidence_pack_id":"evidence-real","observed":true}'::jsonb
            )
            """,
            f"signal-{suffix}",
            tenant,
            workspace,
        )
        outcome = await conn.fetchval(
            """
            INSERT INTO prediction_outcomes(
                tenant_id, workspace_id, signal_id, action_taken,
                actual_value, outcome_summary, evaluation_status,
                evaluation_rule_version, evaluated_at, evaluated_by
            ) VALUES(
                $1, $2, $3, 'review', 12, 'Durable result', 'hit',
                'margin-evaluation.v1', '2026-07-31T00:00:00Z',
                'evaluation-engine'
            ) RETURNING id
            """,
            tenant,
            workspace,
            f"signal-{suffix}",
        )
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO prediction_outcomes(
                        tenant_id, workspace_id, signal_id, action_taken,
                        outcome_summary, evaluation_status
                    ) VALUES($1, $2, $3, 'review', 'Partial evaluation', 'hit')
                    """,
                    tenant,
                    workspace,
                    f"signal-{suffix}",
                )
        await conn.execute(
            """
            INSERT INTO calibration_states(
                state_id, tenant_id, workspace_id, calibration_group,
                model_version, prior, posterior, metrics, reproducibility_hash
            ) VALUES(
                'legacy-state', $1, $2, $3, 'bayesian_calibration.v1',
                '{"alpha":1,"beta":1}', '{"alpha":1,"beta":1}',
                '{"complete":true,"provenance_complete":true}',
                'legacy-state-hash'
            )
            """,
            tenant,
            workspace,
            GROUP,
        )
        await conn.execute(
            "ALTER TABLE calibration_observations "
            "DISABLE TRIGGER calibration_observation_authority"
        )
        await conn.execute(
            "ALTER TABLE calibration_observations "
            "ALTER COLUMN idempotency_key DROP NOT NULL, "
            "ALTER COLUMN evidence_digest DROP NOT NULL"
        )
        common = (
            tenant,
            workspace,
            "forged",
            999999,
            -999999,
            "miss",
            GROUP,
        )
        await conn.execute(
            """
            INSERT INTO calibration_observations(
                observation_id, idempotency_key, evidence_digest,
                tenant_id, workspace_id, source_type, source_id,
                predicted_metric, predicted_value, actual_value, actual_status,
                model_version, calibration_group, reproducibility_hash
            ) VALUES(
                'legacy-option', NULL, NULL, $1, $2, 'decision_option',
                'option-fabricated', $3, $4, $5, $6,
                'bayesian_calibration.v1', $7, 'legacy-hash-option'
            )
            """,
            *common,
        )
        await conn.execute(
            """
            INSERT INTO calibration_observations(
                observation_id, idempotency_key, evidence_digest,
                tenant_id, workspace_id, source_type, source_id,
                predicted_metric, predicted_value, actual_value, actual_status,
                model_version, calibration_group, reproducibility_hash
            ) VALUES(
                'legacy-outcome', NULL, NULL, $1, $2, 'prediction_outcome',
                $3, 'forged', 999999, -999999, 'miss',
                'bayesian_calibration.v1', $4, 'legacy-hash-outcome'
            )
            """,
            tenant,
            workspace,
            str(outcome),
            GROUP,
        )
        await conn.execute(
            "ALTER TABLE calibration_observations "
            "ENABLE TRIGGER calibration_observation_authority"
        )
        sql = MIGRATION.read_text(encoding="utf-8")
        await conn.execute(sql)
        first_state_update = await conn.fetchval(
            "SELECT updated_at FROM calibration_states WHERE state_id='legacy-state'"
        )
        first = [
            dict(row)
            for row in await conn.fetch(
                """
                SELECT observation_id, provenance_status, provenance_reason,
                       predicted_metric, predicted_value, actual_value,
                       actual_status, evaluation_rule_version, idempotency_key,
                       evidence_digest, legacy_claims
                  FROM calibration_observations
                 WHERE workspace_id=$1
                 ORDER BY observation_id
                """,
                workspace,
            )
        ]
        await conn.execute(sql)
        second_state_update = await conn.fetchval(
            "SELECT updated_at FROM calibration_states WHERE state_id='legacy-state'"
        )
        second = [
            dict(row)
            for row in await conn.fetch(
                """
                SELECT observation_id, provenance_status, provenance_reason,
                       predicted_metric, predicted_value, actual_value,
                       actual_status, evaluation_rule_version, idempotency_key,
                       evidence_digest, legacy_claims
                  FROM calibration_observations
                 WHERE workspace_id=$1
                 ORDER BY observation_id
                """,
                workspace,
            )
        ]
        assert second == first
        assert second_state_update == first_state_update
        option, verified = first
        assert option["provenance_status"] == "quarantined"
        assert "decision_option" in option["provenance_reason"]
        assert option["legacy_claims"] is not None
        assert verified["provenance_status"] == "verified"
        assert verified["actual_status"] == "hit"
        assert verified["predicted_metric"] == "margin"
        assert float(verified["predicted_value"]) == 10
        assert float(verified["actual_value"]) == 12
        assert verified["evaluation_rule_version"] == "margin-evaluation.v1"
        assert verified["idempotency_key"]
        assert verified["evidence_digest"]
    finally:
        await transaction.rollback()
        await conn.close()
