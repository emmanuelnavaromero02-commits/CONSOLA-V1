from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99e_operational_native_rls.sql"
COMPLETION = REPO / "infra" / "init" / "99f_native_rls_completion.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_operational_native_rls_migration_exists_and_forces_rls():
    sql = _read(MIGRATION)
    assert "omega_rls_workspace_matches" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "NOBYPASSRLS" in sql


def test_operational_native_rls_covers_workspace_scoped_surfaces():
    sql = _read(MIGRATION) + "\n" + _read(COMPLETION)
    for table in (
        "datasets",
        "decisions",
        "decision_actions",
        "control_room_items",
        "control_room_action_executions",
        "token_usage",
        "vault_entries",
        "vault_access_log",
        "agents",
        "agent_runs",
        "tenant_entitlements",
        "cartridge_installations",
        "conversation_messages",
        "pipeline_runs",
        "copilot_goals",
        "copilot_lessons",
        "users",
        "workspaces",
        "user_workspace_roles",
    ):
        assert table in sql


def test_operational_native_rls_targets_real_service_roles_not_test_roles():
    sql = _read(MIGRATION) + "\n" + _read(COMPLETION)
    for role in ("omega_workspace", "omega_mcp_infra", "omega_vault", "omega_airflow_dag"):
        assert role in sql
    assert "rls_cross_tenant_reader" not in sql
    assert "request.jwt.claims" not in sql


def test_native_rls_completion_forces_remaining_scoped_tables():
    sql = _read(COMPLETION)
    assert "omega_rls_tenant_matches" in sql
    assert "Auth bootstrap exception" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "NOBYPASSRLS" in sql
    for table in (
        "pipeline_runs",
        "copilot_goals",
        "copilot_lessons",
        "users",
        "workspaces",
        "user_workspace_roles",
    ):
        assert table in sql
    assert "99f_native_rls_completion.sql" in sql


def test_tenant_services_set_db_scope_before_rls_tables():
    workspace_main = _read(REPO / "workspace/app/main.py")
    workspace_session = _read(REPO / "workspace/app/services/session.py")
    workspace_tokens = _read(REPO / "workspace/app/services/token_store.py")
    vault = _read(REPO / "vault/app/main.py")
    mcp_agents = _read(REPO / "mcp-infra/app/tools/agents.py")

    for source in (workspace_main, workspace_session, workspace_tokens, vault, mcp_agents):
        assert "set_config('app.tenant_id'" in source
        assert "set_config('app.workspace_id'" in source


def test_cross_tenant_live_test_uses_production_rls_policy():
    test_source = _read(REPO / "console/tests/test_cross_tenant_api_isolation.py")
    assert "datasets_tenant_workspace_rls" in test_source
    assert "OMEGA_WORKSPACE_ROLE" in test_source
    assert "CREATE POLICY cross_tenant_dataset_select" not in test_source
    assert "rls_cross_tenant_reader" not in test_source
