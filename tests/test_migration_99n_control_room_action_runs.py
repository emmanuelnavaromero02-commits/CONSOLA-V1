from __future__ import annotations

from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "infra/init/99n_control_room_action_runs.sql"


def test_control_room_action_runs_migration_exists_and_tracks_itself():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS action_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS action_run_events" in sql
    assert "99n_control_room_action_runs.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_action_runs_are_tenant_workspace_scoped_and_fk_to_items():
    sql = MIGRATION.read_text(encoding="utf-8")
    action_runs_block = sql.split("CREATE TABLE IF NOT EXISTS action_runs", 1)[1].split("CREATE UNIQUE INDEX", 1)[0]
    action_run_events_block = sql.split("CREATE TABLE IF NOT EXISTS action_run_events", 1)[1].split("CREATE INDEX", 1)[0]

    for block in (action_runs_block, action_run_events_block):
        assert "tenant_id" in block
        assert "workspace_id" in block
    assert "FOREIGN KEY (workspace_id, item_id)" in action_runs_block
    assert "REFERENCES control_room_items(workspace_id, item_id)" in action_runs_block
    assert "FOREIGN KEY (workspace_id, action_run_id)" in action_run_events_block
    assert "REFERENCES action_runs(workspace_id, id)" in action_run_events_block
    assert "REFERENCES action_runs(id) ON DELETE CASCADE" not in action_run_events_block


def test_action_runs_have_idempotency_indexes_and_status_contract():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "action_runs_workspace_idempotency_idx" in sql
    assert "action_runs_workspace_id_idx" in sql
    assert "UNIQUE" in sql
    assert "dry_run_completed" in sql
    assert "dry_run_failed" in sql
    assert "blocked" in sql
    assert "completed" in sql
    assert "failed" in sql


def test_action_runs_rls_is_forced_and_fail_closed():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "WITH CHECK" in sql
    assert "omega_console" in sql
    assert "omega_workspace" in sql


def test_action_runs_migration_seeds_new_internal_templates_for_existing_volumes():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "INSERT INTO control_room_action_templates" in sql
    assert "create_investigation_note" in sql
    assert "mark_decision_for_monitoring" in sql
    assert "ON CONFLICT (template_id) DO UPDATE" in sql
    assert '"external_write": false' in sql
