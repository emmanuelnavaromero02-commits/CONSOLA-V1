from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "infra/init/99o_agent_monitor_control_room_alerts.sql"


def test_agent_monitor_alerts_migration_tracks_advisory_path():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "control_room_items_agent_alerts_idx" in sql
    assert "item_kind = 'agent_alert'" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON control_room_items TO omega_mcp_infra" in sql
    assert "GRANT SELECT, INSERT ON control_room_item_events TO omega_mcp_infra" in sql
    assert "99o_agent_monitor_control_room_alerts.sql" in sql
