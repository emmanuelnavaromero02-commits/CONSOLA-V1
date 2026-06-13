from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "infra/init/99o_decision_intelligence_history.sql"
)


def test_decision_intelligence_history_migration_exists_and_tracks_itself():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS intelligence_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS decision_intelligence_snapshots" in sql
    assert "99o_decision_intelligence_history.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_intelligence_runs_have_required_counts_and_run_contract():
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE IF NOT EXISTS intelligence_runs", 1)[1].split(
        "CREATE TABLE IF NOT EXISTS decision_intelligence_snapshots", 1
    )[0]

    for needle in (
        "tenant_id",
        "workspace_id",
        "run_ref",
        "run_mode",
        "status",
        "datasets_evaluated",
        "signals_generated",
        "signals_skipped",
        "dataset_unavailable_count",
        "insufficient_history_count",
        "app_version",
        "deploy_ref",
        "owner_user_id",
    ):
        assert needle in block
    assert "manual" in block
    assert "scheduled" in block
    assert "backtest" in block
    assert "smoke" in block
    assert "not_ready" in block


def test_snapshots_are_append_only_scoped_and_linkable_to_outcomes():
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE IF NOT EXISTS decision_intelligence_snapshots", 1)[
        1
    ].split("CREATE INDEX", 1)[0]

    for needle in (
        "tenant_id",
        "workspace_id",
        "signal_id",
        "control_room_item_id",
        "evidence_pack_id",
        "intelligence_run_id",
        "decision_intelligence",
        "recommended_decision",
        "anomaly_probability",
        "expected_impact_value",
        "data_quality_status",
        "method",
        "outcome_id",
        "outcome_observed_at",
        "outcome_status",
        "measured_impact",
        "calibration_status",
    ):
        assert needle in block
    assert "REFERENCES intelligence_signals(workspace_id, signal_id)" in block
    assert "REFERENCES control_room_items(workspace_id, item_id)" in block
    assert "REFERENCES prediction_outcomes(id) ON DELETE SET NULL" in block
    assert "decision_intelligence_version" in block


def test_decision_history_rls_is_forced_and_grants_sequences():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "omega_workspace" in sql
    assert "omega_console" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON intelligence_runs TO omega_console" in sql
    assert (
        "GRANT USAGE, SELECT ON SEQUENCE intelligence_runs_id_seq TO omega_console"
        in sql
    )
    assert "decision_intelligence_snapshots_calibration_idx" in sql
