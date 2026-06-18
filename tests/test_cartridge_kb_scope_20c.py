"""Prompt 20C cartridge KB scope and residual isolation contracts."""

from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CARTRIDGE_SCOPE_20C = (
    "hubspot",
    "replicon",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
)
OBSERVABILITY_TABLES = ("jobs", "run_logs", "extraction_runs", "kb_runs")


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_20c_cartridge_kb_tools_fail_closed_without_signed_workspace_scope():
    for cartridge in CARTRIDGE_SCOPE_20C:
        request_context = _read(f"cartridges/{cartridge}/app/core/request_context.py")
        kb_service = _read(f"cartridges/{cartridge}/app/services/kb_service.py")
        duckdb_service = _read(f"cartridges/{cartridge}/app/services/duckdb_service.py")
        mcp_server = _read(f"cartridges/{cartridge}/app/mcp_server.py")
        main = _read(f"cartridges/{cartridge}/app/main.py")

        assert "def require_tenant_workspace_scope" in request_context
        assert "verify_security_context(ctx)" in request_context
        assert "security_context tenant/workspace scope is required" in request_context
        assert "security_context = require_tenant_workspace_scope" in kb_service
        assert "resolved_sql = _scope_kb_sql(sql, security_context)" in kb_service
        assert "required_scope=scoped_prefix(security_context)" in kb_service
        assert "require_tenant_workspace_scope(security_context)" in duckdb_service
        assert "KB output path is outside the active tenant/workspace scope" in duckdb_service
        assert "security_context_denied" in mcp_server
        assert "required_scope=scope" in mcp_server
        assert "x-security-context" in main
        assert "require_tenant_workspace_scope()" in main


def test_20c_sql_guard_canonicalizes_s3_paths_and_requires_exact_scope():
    for cartridge in CARTRIDGE_SCOPE_20C:
        sql_guard = _read(f"cartridges/{cartridge}/app/core/sql_guard.py")

        assert "def _canonical_s3_path" in sql_guard
        assert "def _has_exact_scope" in sql_guard
        assert "required_scope: str | None = None" in sql_guard
        assert "path must stay inside the active tenant/workspace scope" in sql_guard
        assert "unquote" in sql_guard
        assert "for _ in range(3)" in sql_guard
        assert "parts[idx : idx + len(scope_parts)] == scope_parts" in sql_guard


def test_20c_hubspot_pii_contract_masks_or_shadows_contact_identifiers():
    entities = _read("cartridges/hubspot/app/config/entities.yaml")
    for field in ("firstname", "lastname", "phone", "company"):
        assert re.search(rf"{field}:\s*masked", entities), field
    for field in ("email", "hubspot_owner_id"):
        assert re.search(rf"{field}:\s*shadowed", entities), field


def test_20c_mcp_infra_denies_unscoped_cartridge_tools_and_canonicalizes_paths():
    source = _read("mcp-infra/app/main.py")

    assert "def _canonical_storage_key" in source
    assert "def _key_has_exact_scope" in source
    assert "unquote" in source
    assert 'tool.startswith("cartridge_")' in source
    assert "cartridge tool lacks tenancy metadata" in source
    assert "_require_scoped_object_path(ctx, key)" in source
    assert "cartridge tools require tenant/workspace scope" in source
    assert "elif tool.startswith(\"cartridge_\")" in source


def test_20c_observability_migration_scopes_runs_and_legacy_rows():
    sql = _read("infra/init/99x_cartridge_kb_scope_20c.sql")

    assert "USING (true)" not in sql
    assert "USING(true)" not in sql.replace(" ", "")
    assert "legacy_unscoped" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    for table in OBSERVABILITY_TABLES:
        assert f"'{table}'" in sql
    assert "ADD COLUMN IF NOT EXISTS tenant_id UUID" in sql
    assert "ADD COLUMN IF NOT EXISTS workspace_id UUID" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "tbl || '_20c_scoped_rls'" in sql
    assert "tbl || '_20c_legacy_platform_audit_rls'" in sql


def test_20c_lessons_global_maps_to_workspace_global_not_cross_tenant_reads():
    service = _read("console/app/services/lessons_service.py")
    router = _read("console/app/routers/copilot_advanced.py")
    migration = _read("infra/init/99x_cartridge_kb_scope_20c.sql")

    assert 'scope = "workspace_global"' in service
    assert "scope='global'" not in service
    assert "OR scope='global'" not in service
    assert "platform_global lessons require platform admin" in router
    assert "WHEN workspace_id IS NOT NULL THEN 'workspace_global'" in migration
    assert "ELSE 'platform_global'" in migration


def test_20c_writeback_adapters_use_pinned_requests_for_configured_urls():
    egress = _read("console/app/services/egress_guard.py")
    http_writeback = _read("console/app/services/adapters/http_writeback.py")
    replicon = _read("console/app/services/adapters/replicon_adapter.py")
    sap = _read("console/app/services/adapters/sap_hcm_adapter.py")

    assert "def pinned_request_sync" in egress
    assert "socket.create_connection((address, port)" in egress
    assert "await egress_guard.pinned_request(" in http_writeback
    assert "egress_guard.pinned_request_sync(" in replicon
    assert sap.count("egress_guard.pinned_request_sync(") >= 2
    for source in (http_writeback, replicon, sap):
        assert "validate_url(" not in source
        assert "httpx.Client" not in source
        assert "httpx.AsyncClient" not in source


def test_20c_dataset_app_and_lineage_reads_by_name_are_workspace_scoped():
    dataset_store = _read("refinement/app/dataset_store.py")
    refinement = _read("refinement/app/main.py")

    assert "name = %s AND (%s::uuid IS NULL OR workspace_id = %s::uuid)" in dataset_store
    assert "dataset name already exists outside the active workspace" in dataset_store
    assert "workspace_id = %s::uuid OR scope_status = 'platform_template'" in refinement
    assert "DELETE FROM analytic_apps WHERE name=%s AND (%s::uuid IS NULL OR workspace_id = %s::uuid)" in refinement
    lineage_block = refinement.split('if tool == "get_lineage":', 1)[1].split('if tool == "describe_source":', 1)[0]
    assert 'store.get_dataset(args["name"], **_dataset_store_scope(sec))' in lineage_block
    assert "if not ds:" in lineage_block
    assert "return _get_lineage(args[\"name\"], args.get(\"limit\", 10))" in lineage_block


def test_20c_scheduled_agent_policy_requires_rls_workspace_match():
    migration = _read("infra/init/99x_cartridge_kb_scope_20c.sql")
    residual = _read("infra/init/99s_remaining_operational_rls.sql")

    for sql in (migration, residual):
        assert "agents_console_refinement_scheduled_read_rls" in sql
        policy = sql.split("agents_console_refinement_scheduled_read_rls", 1)[1]
        assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in policy
        assert "workspace_id IS NOT NULL" not in policy.split(");", 1)[0]
