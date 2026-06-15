from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra" / "init" / "99p_operational_rls_console_refinement.sql"
ALLOWLIST = REPO / "infra" / "init" / "99m_company_onboarding_rls.sql"

CRITICAL_TABLES = (
    "datasets",
    "decisions",
    "control_room_items",
    "control_room_item_events",
    "control_room_action_executions",
    "pipeline_runs",
    "copilot_goals",
    "copilot_lessons",
    "rag_sources",
    "rag_chunks",
    "entity_watermarks",
    "token_usage",
    "user_cartridge_overrides",
    "marketplace_orders",
    "tenant_entitlements",
    "cartridge_installations",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_console_refinement_backstop_migration_has_no_unscoped_owner_policy():
    sql = _read(MIGRATION)

    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql
    assert "omega_console" in sql
    assert "omega_refinement" in sql
    assert "ALTER ROLE omega_console NOBYPASSRLS" in sql
    assert "ALTER ROLE omega_refinement NOBYPASSRLS" in sql
    assert "omega_rls_workspace_matches" in sql


def test_console_refinement_backstop_drops_platform_owner_escape_hatches():
    sql = _read(MIGRATION)

    for table in CRITICAL_TABLES:
        assert f"'{table}'" in sql
    assert "DROP POLICY IF EXISTS %I" in sql
    assert "tbl || '_platform_owner_rls'" in sql
    assert "console_refinement_scope_rls" in sql


def test_critical_operational_tables_are_not_platform_owner_allowlisted():
    allowlist_sql = _read(ALLOWLIST)

    for table in CRITICAL_TABLES:
        assert f"('{table}'" not in allowlist_sql


def test_login_and_provisioning_allowlist_stays_explicit():
    allowlist_sql = _read(ALLOWLIST)

    for table in ("tenants", "workspaces", "users", "user_workspace_roles"):
        assert f"('{table}'" in allowlist_sql
