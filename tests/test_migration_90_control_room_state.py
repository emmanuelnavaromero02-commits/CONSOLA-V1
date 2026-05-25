from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "infra/init/90_control_room_state.sql"


def test_migration_90_creates_control_room_state_tables():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS control_room_items" in sql
    assert "CREATE TABLE IF NOT EXISTS control_room_item_events" in sql
    assert "PRIMARY KEY (workspace_id, item_id)" in sql
    assert "REFERENCES decisions(id) ON DELETE SET NULL" in sql
    assert "control_room_items_status_chk" in sql


def test_migration_90_is_idempotent_and_self_registers():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE INDEX IF NOT EXISTS control_room_items_workspace_status_idx" in sql
    assert "INSERT INTO schema_migrations (filename, applied_at)" in sql
    assert "'90_control_room_state.sql'" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
