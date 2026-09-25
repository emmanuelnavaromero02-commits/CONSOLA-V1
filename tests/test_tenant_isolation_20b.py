from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "infra/init/99w_tenant_isolation_20b.sql"

TABLE_CLASSIFICATION = {
    "copilot_drafts": "user_private",
    "workflow_runs": "workspace_data",
    "workflow_steps": "workspace_data",
    "user_facts": "user_private",
    "user_preferences": "user_private",
    "conversation_memory_summary": "user_private",
    "analytic_apps": "platform_template",
    "data_catalog": "workspace_data",
    "data_relationships": "workspace_data",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_20b_migration_classifies_all_audited_tables_and_preserves_legacy_rows():
    sql = _read(MIGRATION)
    for table, classification in TABLE_CLASSIFICATION.items():
        assert table in sql
        assert classification in sql
    assert "legacy_unscoped" in sql
    assert "platform-admin/service audit" in sql
    assert "scope_status" in sql


def test_20b_migration_enforces_rls_force_and_no_using_true():
    sql = _read(MIGRATION)
    assert "USING (true)" not in sql
    assert "USING(true)" not in sql.replace(" ", "")
    for table in TABLE_CLASSIFICATION:
        assert table in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "NOBYPASSRLS" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql


def test_20b_migration_removes_global_uniqueness_and_default_console_dml():
    sql = _read(MIGRATION)
    assert "DROP CONSTRAINT IF EXISTS data_catalog_dataset_column_name_key" in sql
    assert "data_catalog_scoped_dataset_column_key" in sql
    assert "data_relationships_scoped_key" in sql
    assert "user_preferences_scoped_key" in sql
    assert "user_facts_scoped_key" in sql
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public" in sql
    assert "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM omega_console" in sql


def test_copilot_paths_use_authenticated_db_scope_and_sql_scope_filters():
    for rel in (
        "console/app/routers/copilot_drafts.py",
        "console/app/routers/copilot_memory.py",
        "console/app/routers/copilot_workflows.py",
        "console/app/services/workflow_executor.py",
        "console/app/services/draft_sender.py",
        "console/app/services/memory_service.py",
    ):
        src = _read(REPO / rel)
        assert "scoped_db_for_user" in src, rel
        assert "scope_status = 'scoped'" in src, rel
        assert "workspace_id" in src, rel


def test_dashboard_security_jobs_and_briefing_filter_scope_in_sql():
    dashboard = _read(REPO / "console/app/routers/dashboard.py")
    assert "pipeline_runs" in dashboard
    assert "workspace_id = $1::uuid" in dashboard
    assert "active_cartridges = active_cartridges or _CARTRIDGES" in dashboard
    assert "if _is_platform_admin(user)" in dashboard

    security = _read(REPO / "console/app/routers/security.py")
    assert "JOIN users u ON lower(u.email) = lower(la.email)" in security
    assert "s.user_id = ANY" in security
    assert "scoped_db_for_user" in security

    jobs = _read(REPO / "console/app/services/job_service.py")
    assert "SELECT * FROM jobs" not in jobs
    assert "COALESCE(args->>'tenant_id', result->>'tenant_id'" in jobs
    assert "COALESCE(args->>'workspace_id', result->>'workspace_id'" in jobs

    proactive = _read(REPO / "console/app/services/proactive_service.py")
    assert "pipeline_runs" in proactive
    assert "COALESCE(args->>'tenant_id', result->>'tenant_id'" in proactive
    assert "user_context=user_context" in proactive


def test_catalog_refinement_and_mcp_infra_set_db_scope_before_catalog_access():
    refinement = _read(REPO / "refinement/app/main.py")
    assert "def _pg_set_scope" in refinement
    assert "set_config('app.tenant_id'" in refinement
    assert "def _default_workspace_security_context" in refinement
    assert "scope_status = 'scoped'" in refinement
    assert "ON CONFLICT (workspace_id, dataset, column_name)" in refinement
    assert (
        "ON CONFLICT (workspace_id, from_dataset, from_column, to_dataset, to_column)"
        in refinement
    )

    duckdb_engine = _read(REPO / "refinement/app/duckdb_engine.py")
    tree = ast.parse(duckdb_engine)
    materialize = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "materialize"
    )
    catalog_calls = [
        node
        for node in ast.walk(materialize)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_update_catalog"
    ]
    assert len(catalog_calls) == 1
    user_context = next(
        keyword.value
        for keyword in catalog_calls[0].keywords
        if keyword.arg == "user_context"
    )
    assert isinstance(user_context, ast.Name)
    assert user_context.id == "user_context"
    assert "workspace_id = EXCLUDED.workspace_id" not in duckdb_engine
    assert "ON CONFLICT (workspace_id, dataset, column_name)" in duckdb_engine

    mcp = _read(REPO / "mcp-infra/app/tools/cartridges.py")
    assert "def _set_pg_scope" in mcp
    assert "def _catalog_scope_sql" in mcp
    assert "scope_status = 'scoped'" in mcp


def test_workspace_cartridge_resolution_sets_scope_before_entitlements():
    deps = _read(REPO / "console/app/dependencies.py")
    assert "from app.services.db_scope import scoped_db" in deps
    assert "SELECT tenant_id::text FROM workspaces" in deps
    assert "scoped_db(p, tenant_id, workspace_id)" in deps


def test_startup_seeders_declare_platform_or_workspace_scope():
    packaged_apps = _read(REPO / "console/app/services/seed_packaged_apps.py")
    assert "tenant_id, workspace_id, scope_status" in packaged_apps
    assert "NULL, NULL, 'platform_template'" in packaged_apps
    assert "scope_status = 'platform_template'" in packaged_apps

    dataset_store = _read(REPO / "refinement/app/dataset_store.py")
    save_dataset = dataset_store.split("def save_dataset", 1)[1].split(
        "def delete_dataset", 1
    )[0]
    assert (
        'workspace_id = ds.get("workspace_id") or _default_workspace_id(cur)'
        in save_dataset
    )
    assert (
        'tenant_id = ds.get("tenant_id") or _tenant_for_workspace(cur, workspace_id)'
        in save_dataset
    )
    assert "_apply_scope(cur, tenant_id, workspace_id)" in save_dataset

    refinement = _read(REPO / "refinement/app/main.py")
    seed_relationships = refinement.split("def _seed_relationships", 1)[1].split(
        "# ── REST API", 1
    )[0]
    assert "_default_workspace_security_context()" in seed_relationships
    assert "tenant_id, workspace_id, scope_status" in seed_relationships
    assert "security_context=security_context" in seed_relationships


def _tenant_like_tables(sql: str) -> set[str]:
    tables: set[str] = set()
    for match in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\);",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        body = match.group(2).lower()
        if any(token in body for token in ("tenant_id", "workspace_id", "user_id")):
            tables.add(match.group(1))
    return tables


def _assert_tenant_like_tables_have_rls(sql: str) -> None:
    for table in _tenant_like_tables(sql):
        table_re = re.escape(table)
        assert re.search(
            rf"ALTER\s+TABLE\s+(?:public\.)?{table_re}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
            sql,
            re.I,
        )
        assert re.search(
            rf"ALTER\s+TABLE\s+(?:public\.)?{table_re}\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
            sql,
            re.I,
        )
    assert "USING (true)" not in sql


def test_new_table_guard_fixture_fails_without_force_rls():
    bad_sql = """
    CREATE TABLE IF NOT EXISTS leaky_table (
        id bigserial primary key,
        tenant_id uuid,
        workspace_id uuid,
        user_id bigint
    );
    ALTER TABLE leaky_table ENABLE ROW LEVEL SECURITY;
    CREATE POLICY leaky_table_all ON leaky_table USING (true);
    """
    with pytest.raises(AssertionError):
        _assert_tenant_like_tables_have_rls(bad_sql)


def test_new_table_guard_accepts_force_rls_without_permissive_policy():
    good_sql = """
    CREATE TABLE IF NOT EXISTS scoped_table (
        id bigserial primary key,
        tenant_id uuid,
        workspace_id uuid,
        user_id bigint
    );
    ALTER TABLE scoped_table ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scoped_table FORCE ROW LEVEL SECURITY;
    CREATE POLICY scoped_table_scope ON scoped_table
      USING (omega_rls_workspace_matches(tenant_id, workspace_id))
      WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));
    """
    _assert_tenant_like_tables_have_rls(good_sql)
