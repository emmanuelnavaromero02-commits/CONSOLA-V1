from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_schema_has_durable_idempotency_and_evidence_digest() -> None:
    sql = (ROOT / "infra/init/99r_bayesian_calibration.sql").read_text(
        encoding="utf-8"
    )
    assert "idempotency_key" in sql
    assert "evidence_digest" in sql
    assert "UNIQUE (workspace_id, idempotency_key)" in sql


def test_upgrade_is_legacy_safe_and_adds_partial_unique_index() -> None:
    migration = (
        ROOT / "infra/init/99zzd_calibration_observation_idempotency.sql"
    ).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS idempotency_key" in migration
    assert "ADD COLUMN IF NOT EXISTS evidence_digest" in migration
    assert "CREATE UNIQUE INDEX" in migration
    assert "WHERE idempotency_key IS NOT NULL" in migration
    assert "UPDATE calibration_observations" not in migration
    assert "REVOKE UPDATE, DELETE ON calibration_observations" in migration
    assert "REVOKE UPDATE, DELETE ON prediction_outcomes" in migration
