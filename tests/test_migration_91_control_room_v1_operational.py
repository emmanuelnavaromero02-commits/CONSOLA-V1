from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "infra/init/91_control_room_v1_operational.sql"


def test_control_room_v1_operational_migration_exists_and_tracks_itself():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ALTER TABLE control_room_items" in sql
    assert "impact_estimate" in sql
    assert "selected_option_id" in sql
    assert "execution_status" in sql
    assert "CREATE TABLE IF NOT EXISTS control_room_action_templates" in sql
    assert "CREATE TABLE IF NOT EXISTS control_room_action_executions" in sql
    assert "CREATE TABLE IF NOT EXISTS control_room_thresholds" in sql
    assert "CREATE TABLE IF NOT EXISTS control_room_lessons" in sql
    assert "91_control_room_v1_operational.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_control_room_v1_seeds_safe_action_templates_only():
    sql = MIGRATION.read_text(encoding="utf-8")

    for template in (
        "restore_data_source",
        "create_followup_task",
        "request_owner_review",
        "prepare_replicon_adjustment",
        "prepare_billing_review",
        "prepare_sap_review",
    ):
        assert template in sql
    assert '"external_write": false' in sql
