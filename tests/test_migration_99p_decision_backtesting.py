from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1] / "infra/init/99p_decision_backtesting.sql"
)


def test_decision_backtesting_migration_exists_and_tracks_itself():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS backtest_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS backtest_results" in sql
    assert "99p_decision_backtesting.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_backtest_runs_have_required_contract_and_modes():
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE IF NOT EXISTS backtest_runs", 1)[1].split(
        "CREATE UNIQUE INDEX", 1
    )[0]

    for needle in (
        "tenant_id",
        "workspace_id",
        "run_ref",
        "run_mode",
        "source_system",
        "source_dataset",
        "metric",
        "periods_evaluated",
        "labels_available",
        "labels_required",
        "insufficient_labeled_data",
        "config",
        "summary",
        "actor_user_id",
        "app_version",
        "deploy_ref",
    ):
        assert needle in block
    assert "historical_replay" in block
    assert "outcome_linked" in block
    assert "fixture_validation" in block
    assert "insufficient_labeled_data" in block
    assert "dataset_unavailable" in block


def test_backtest_results_are_scoped_and_keep_label_sources_separate():
    sql = MIGRATION.read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE IF NOT EXISTS backtest_results", 1)[1].split(
        "CREATE INDEX", 1
    )[0]

    for needle in (
        "tenant_id",
        "workspace_id",
        "backtest_run_id",
        "snapshot_id",
        "signal_id",
        "control_room_item_id",
        "source_system",
        "source_dataset",
        "gold_table",
        "metric",
        "entity_key",
        "period_key",
        "as_of_period",
        "anomaly_probability",
        "predicted_label",
        "actual_label",
        "label_source",
        "recommended_decision",
        "expected_impact_value",
        "measured_impact",
        "is_true_positive",
        "is_false_positive",
        "is_true_negative",
        "is_false_negative",
        "result",
    ):
        assert needle in block
    assert "outcome" in block
    assert "historical_rule" in block
    assert "fixture" in block
    assert "unavailable" in block


def test_backtesting_rls_is_forced_and_granted_only_to_console():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON backtest_runs TO omega_console" in sql
    assert "GRANT SELECT, INSERT ON backtest_results TO omega_console" in sql
    assert (
        "GRANT USAGE, SELECT ON SEQUENCE backtest_runs_id_seq TO omega_console" in sql
    )
