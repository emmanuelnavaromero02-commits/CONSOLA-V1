from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/init/99zze_calibration_authoritative_reconciliation.sql"
AUTHORITY = ROOT / "infra/init/99zzf_calibration_observation_authority.sql"


def test_legacy_reconciliation_is_explicit_idempotent_and_non_destructive() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    required = (
        "provenance_status",
        "provenance_reason",
        "provenance_checked_at",
        "authoritative_calibration_group",
        "evaluation_rule_version",
        "evaluation_status",
        "quarantined",
        "verified",
        "decision_option",
        "prediction_outcome",
        "ON CONFLICT (filename) DO NOTHING",
    )
    assert all(token in sql for token in required)
    assert "DELETE FROM calibration_observations" not in sql
    assert "DROP TABLE calibration_observations" not in sql
    assert "UPDATE calibration_observations" in sql
    assert ") IS TRUE) NOT VALID" in sql


def test_recompute_queries_only_verified_authoritative_observations() -> None:
    source = (
        ROOT / "console/app/services/intelligence/calibration_recompute_batch.py"
    ).read_text(encoding="utf-8")
    assert "provenance_status = 'verified'" in source
    assert "resolve_authoritative_observation" in source
    assert "resolve_source_provenance" not in source


def test_runtime_provenance_columns_are_server_owned_and_deduplicated() -> None:
    sql = AUTHORITY.read_text(encoding="utf-8")
    assert "enforce_calibration_observation_authority" in sql
    assert "REVOKE INSERT ON calibration_observations FROM omega_console" in sql
    grant = sql.split("GRANT INSERT (", 1)[1].split(") ON calibration_observations", 1)[
        0
    ]
    assert "provenance_status" not in grant
    assert "authoritative_calibration_group" not in grant
    assert "manual fixture persistence is test-harness only" in sql
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "calibration_observations_verified_source_uidx" in migration
