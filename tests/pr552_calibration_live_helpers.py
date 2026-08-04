from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from app.services.intelligence import persistence


def _real_signal(
    signal_id: str,
    *,
    entity_id: str,
    actual_value: float,
    expected_value: float,
    predicted_value: float | None,
    signal_subtype: str,
    observed_at: str,
) -> dict:
    return {
        "signal_id": signal_id,
        "cartridge_id": "replicon",
        "dataset": "gold_margin",
        "source_system": "replicon",
        "source_dataset": "gold_margin",
        "domain": "Operacion",
        "entity_kind": "project",
        "entity_id": entity_id,
        "entity_label": entity_id,
        "metric": "margin",
        "period_key": observed_at[:10],
        "freshness_at": observed_at,
        "actual_value": actual_value,
        "expected_value": expected_value,
        "predicted_value": predicted_value,
        "prediction_horizon_days": 1,
        "prediction_method": "real_extraction.v1",
        "signal_subtype": signal_subtype,
        "deviation_value": actual_value - expected_value,
        "deviation_pct": 0,
        "severity": "low",
        "signal_type": "watch" if signal_subtype == "observed" else "opportunity",
        "confidence": 1.0,
        "summary": f"Real extracted {signal_subtype} signal",
        "evidence_pack_id": f"evidence-{signal_id}",
    }


async def seed_real_signals(dsn: str) -> tuple[str, str, str]:
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
        created_at = datetime.now(timezone.utc)
        await persistence.persist_signal(
            conn,
            str(tenant),
            str(workspace),
            _real_signal(
                "signal-calibration",
                entity_id="P-1",
                actual_value=12,
                expected_value=10,
                predicted_value=12,
                signal_subtype="future_opportunity",
                observed_at=created_at.isoformat(),
            ),
            None,
        )
        await persistence.persist_signal(
            conn,
            str(tenant),
            str(workspace),
            _real_signal(
                "signal-calibration-observed",
                entity_id="P-1",
                actual_value=12,
                expected_value=10,
                predicted_value=None,
                signal_subtype="observed",
                observed_at=(created_at + timedelta(days=2)).isoformat(),
            ),
            None,
        )
        await persistence.persist_signal(
            conn,
            str(tenant),
            str(workspace),
            _real_signal(
                "signal-calibration-unobserved",
                entity_id="P-2",
                actual_value=15,
                expected_value=10,
                predicted_value=15,
                signal_subtype="future_opportunity",
                observed_at=created_at.isoformat(),
            ),
            None,
        )
        for index in range(2, 6):
            entity_id = f"P-{index + 1}"
            await persistence.persist_signal(
                conn,
                str(tenant),
                str(workspace),
                _real_signal(
                    f"signal-calibration-{index}",
                    entity_id=entity_id,
                    actual_value=12,
                    expected_value=10,
                    predicted_value=12,
                    signal_subtype="future_opportunity",
                    observed_at=created_at.isoformat(),
                ),
                None,
            )
            await persistence.persist_signal(
                conn,
                str(tenant),
                str(workspace),
                _real_signal(
                    f"signal-calibration-{index}-observed",
                    entity_id=entity_id,
                    actual_value=12,
                    expected_value=10,
                    predicted_value=None,
                    signal_subtype="observed",
                    observed_at=(created_at + timedelta(days=2)).isoformat(),
                ),
                None,
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


async def assert_calibration_writer_privileges(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        privileges = await conn.fetchrow(
            """
            SELECT
              has_table_privilege(
                'omega_console', 'calibration_observations', 'UPDATE'
              ) AS observation_update,
              has_any_column_privilege(
                'omega_console', 'calibration_observations', 'INSERT'
              ) AS observation_column_insert,
              has_table_privilege(
                'omega_console', 'prediction_outcomes', 'UPDATE'
              ) AS outcome_update,
              has_any_column_privilege(
                'omega_console', 'prediction_outcomes', 'INSERT'
              ) AS outcome_column_insert,
              has_table_privilege(
                'omega_console', 'calibration_states', 'INSERT'
              ) AS state_insert,
              has_table_privilege(
                'omega_console', 'calibration_states', 'UPDATE'
              ) AS state_update,
              has_function_privilege(
                'omega_console',
                'public.record_calibration_observation(jsonb)',
                'EXECUTE'
              ) AS observation_writer,
              has_function_privilege(
                'omega_console',
                'public.upsert_calibration_state(jsonb)',
                'EXECUTE'
              ) AS state_writer,
              has_sequence_privilege(
                'omega_console', 'public.prediction_outcomes_id_seq', 'USAGE'
              ) AS outcome_sequence_usage,
              has_function_privilege(
                'omega_console',
                'public.authoritative_calibration_parent_evidence('
                'uuid,uuid,text,text)',
                'EXECUTE'
              ) AS parent_evidence_reader
            """
        )
    finally:
        await conn.close()
    assert dict(privileges) == {
        "observation_update": False,
        "observation_column_insert": False,
        "outcome_update": False,
        "outcome_column_insert": False,
        "state_insert": False,
        "state_update": False,
        "observation_writer": True,
        "state_writer": True,
        "outcome_sequence_usage": False,
        "parent_evidence_reader": False,
    }


__all__ = ("assert_calibration_writer_privileges", "seed_real_signals")
