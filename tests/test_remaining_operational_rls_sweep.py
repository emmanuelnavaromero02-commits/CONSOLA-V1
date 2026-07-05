from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99s_remaining_operational_rls.sql"
AUTH_ALLOWLIST = REPO / "infra" / "init" / "99m_company_onboarding_rls.sql"

OPERATIONAL_TABLES = (
    "action_runs",
    "action_run_events",
    "agent_runs",
    "agents",
    "backtest_results",
    "backtest_runs",
    "cartridge_installation_events",
    "conversation_messages",
    "conversations",
    "decision_actions",
    "decision_intelligence_snapshots",
    "intelligence_runs",
    "vault_access_log",
    "vault_entries",
)

AUTH_BOOTSTRAP_TABLES = (
    "users",
    "user_workspace_roles",
    "workspaces",
)

CONSUMER_MARKERS = {
    "action_runs": ("console/app/services/control_room/execution.py", "FROM action_runs"),
    "action_run_events": (
        "console/app/services/control_room/execution.py",
        "INSERT INTO action_run_events",
    ),
    "agent_runs": ("console/app/services/agent_runtime.py", "INSERT INTO agent_runs"),
    "agents": ("console/app/services/agent_service.py", "SELECT * FROM agents"),
    "backtest_results": (
        "console/app/services/intelligence/backtesting.py",
        "FROM backtest_results",
    ),
    "backtest_runs": (
        "console/app/services/intelligence/backtesting.py",
        "INSERT INTO backtest_runs",
    ),
    "cartridge_installation_events": (
        "infra/init/99e_operational_native_rls.sql",
        "cartridge_installation_events_workspace_rls",
    ),
    "conversation_messages": (
        "console/app/services/copilot_service.py",
        "INSERT INTO conversation_messages",
    ),
    "conversations": ("console/app/services/copilot_service.py", "FROM conversations"),
    "decision_actions": (
        "console/app/services/control_room/execution.py",
        "INSERT INTO decision_actions",
    ),
    "decision_intelligence_snapshots": (
        "console/app/services/intelligence/history.py",
        "decision_intelligence_snapshots",
    ),
    "intelligence_runs": (
        "console/app/services/intelligence/history.py",
        "INSERT INTO intelligence_runs",
    ),
    "vault_access_log": ("vault/app/main.py", "INSERT INTO vault_access_log"),
    "vault_entries": ("vault/app/main.py", "FROM vault_entries"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_remaining_operational_rls_migration_closes_platform_owner_policies():
    sql = _read(MIGRATION)

    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert " BYPASSRLS" not in sql
    assert "NOBYPASSRLS" in sql
    assert "omega_rls_workspace_matches" in sql

    for table in OPERATIONAL_TABLES:
        assert table in sql

    assert "tbl || '_platform_owner_rls'" in sql
    for table in (
        "agents",
        "cartridge_installation_events",
        "conversation_messages",
        "decision_actions",
    ):
        assert f"{table}_platform_owner_rls" in sql
        assert f"DROP POLICY IF EXISTS {table}_platform_owner_rls" in sql

    assert "agents_console_refinement_global_template_rls" in sql
    assert "agents_console_refinement_scheduled_read_rls" in sql


def test_auth_bootstrap_platform_owner_exceptions_remain_explicit():
    allowlist_sql = _read(AUTH_ALLOWLIST)
    migration_sql = _read(MIGRATION)

    for table in AUTH_BOOTSTRAP_TABLES:
        assert f"('{table}'" in allowlist_sql
        assert f"DROP POLICY IF EXISTS {table}_platform_owner_rls" not in migration_sql


def test_every_closed_operational_table_has_a_real_consumer_marker():
    for table, (relative_path, marker) in CONSUMER_MARKERS.items():
        assert table in OPERATIONAL_TABLES
        source = _read(REPO / relative_path)
        assert marker in source, f"{table} must be tied to a real runtime path"


def test_agent_runtime_and_watchdog_loaders_set_scope_before_scoped_agent_reads():
    runtime = _read(REPO / "console/app/services/agent_runtime.py")
    v1_agents = _read(REPO / "console/app/routers/v1/agents.py")
    invocation = _read(REPO / "console/app/domains/agentops/invocation.py")
    streaming = _read(REPO / "console/app/domains/agentops/streaming.py")
    watchdog = _read(REPO / "console/app/services/watchdog_registry.py")
    service = _read(REPO / "console/app/services/agent_service.py")

    assert "from app.services.db_scope import scoped_db" in runtime
    assert "load_agent(agent_id, user_context=user)" in v1_agents
    assert "load_agent(agent_id, user_context=user)" in invocation
    assert "load_agent(agent_id, user_context=user)" in streaming
    assert "load_agent_by_slug(" in watchdog
    assert "user_context=user" in watchdog
    assert "SET_SCOPE_SQL" in service
    assert "async with conn.transaction()" in service


def test_copilot_conversation_paths_use_runtime_db_scope():
    service = _read(REPO / "console/app/services/copilot_service.py")
    router = _read(REPO / "console/app/routers/copilot.py")

    assert "from app.services.db_scope import scoped_db_for_user" in service
    assert "async with scoped_db_for_user(pool, user)" in service
    assert "async with pool.acquire() as conn" not in service
    assert "create_conversation(\n        user=user" in router
    assert "list_conversations(\n        user=user" in router
