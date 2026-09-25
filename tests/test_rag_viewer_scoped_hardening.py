from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONSOLE_MAIN = ROOT / "console/app/main.py"
SECURITY_CONTEXT = ROOT / "console/app/services/security_context.py"
MCP_MAIN = ROOT / "mcp-infra/app/main.py"
RAG_STORE = ROOT / "mcp-infra/app/rag/store.py"
PIPELINE_TOOLS = ROOT / "mcp-infra/app/tools/pipeline.py"
CARTRIDGE_TOOLS = ROOT / "mcp-infra/app/tools/cartridges.py"
FRESHNESS = ROOT / "console/app/routers/freshness.py"
MIGRATION = ROOT / "infra/init/99n_rag_viewer_scoped_rls.sql"
REPLICON_DAG = ROOT / "cartridges/replicon/dags/replicon_extract.py"
WATERMARK_SERVICES = [
    ROOT / "cartridges/replicon/app/services/watermark_service.py",
    ROOT / "cartridges/hubspot/app/services/watermark_service.py",
    ROOT / "cartridges/salesforce/app/services/watermark_service.py",
    ROOT / "cartridges/sap_hcm/app/services/watermark_service.py",
    ROOT / "cartridges/sap_s4hana/app/services/watermark_service.py",
    ROOT / "cartridges/sap_successfactors/app/services/watermark_service.py",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workspace_security_context_never_grants_wildcard_from_permissions(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "x" * 64)
    monkeypatch.setenv("INTERNAL_API_KEY", "y" * 64)
    sys.path.insert(0, str(ROOT / "console"))
    try:
        module = importlib.import_module("app.services.security_context")
        user = {
            "id": 42,
            "email": "tenant@example.com",
            "role": "user",
            "workspace_role": "tenant_admin",
            "active_tenant_id": "00000000-0000-0000-0000-000000000001",
            "active_workspace_id": "00000000-0000-0000-0000-000000000002",
            "permissions": [
                "cartridges.read",
                "datasets.read",
                "vault.connections.read",
            ],
        }
        ctx = module.build_security_context(user)
    finally:
        sys.path.pop(0)

    assert ctx["allowed_cartridges"] == []


def test_rag_store_filters_by_scope_before_vector_ranking():
    source = _read(RAG_STORE)

    assert "{alias}.tenant_id::text = $SCOPE_TENANT" in source
    assert "{alias}.workspace_id::text = $SCOPE_WORKSPACE" in source
    assert "await _set_rls_context(conn, scope)" in source
    assert "ORDER BY c.embedding <=> $1" in source
    assert source.index("{alias}.tenant_id::text = $SCOPE_TENANT") < source.index(
        "ORDER BY c.embedding <=> $1"
    )


def test_mcp_rag_tools_receive_server_owned_scope():
    source = _read(MCP_MAIN)

    assert "def _require_rag_context_scope" in source
    assert "if tool in _RAG_READ_TOOLS | _RAG_WRITE_TOOLS" in source
    assert "_require_rag_context_scope(ctx)" in source
    assert 'args["security_context"] = ctx' in source
    assert "_rag_row_allowed" in source


def test_watermark_tools_are_workspace_scoped():
    source = _read(PIPELINE_TOOLS)
    gateway = _read(MCP_MAIN)
    migration = _read(MIGRATION)
    cartridge_tools = _read(CARTRIDGE_TOOLS)

    assert "watermark_scope" in source
    assert "ON CONFLICT (watermark_scope, cartridge_id, entity_name)" in source
    assert re.search(
        r'tool\s+in\s+\{\s*"watermark_get"\s*,\s*"watermark_set"\s*,?\s*\}',
        gateway,
    )
    assert 'args["tenant_id"]' in gateway
    assert 'args["workspace_id"]' in gateway
    assert "cartridge_list_entities" in gateway
    assert "w.watermark_scope = %s" in cartridge_tools
    assert "PRIMARY KEY (watermark_scope, cartridge_id, entity_name)" in migration


def test_direct_cartridge_watermark_services_use_scoped_conflict_key():
    for path in [*WATERMARK_SERVICES, REPLICON_DAG]:
        source = _read(path)
        assert "watermark_scope" in source, path
        assert (
            "ON CONFLICT (watermark_scope, cartridge_id, entity_name)" in source
        ), path
        assert "ON CONFLICT (cartridge_id, entity_name)" not in source, path
        assert "set_config('app.tenant_id'" in source, path


def test_successfactors_watermark_update_does_not_move_backwards():
    source = _read(
        ROOT / "cartridges/sap_successfactors/app/services/watermark_service.py"
    )
    assert (
        "WHERE COALESCE(entity_watermarks.last_watermark_value, '') "
        "<= EXCLUDED.last_watermark_value"
    ) in source


def test_technical_viewer_endpoints_have_backend_scope_guards():
    source = _read(CONSOLE_MAIN)

    assert "_require_technical_source_access(user, source)" in source
    assert "_require_technical_cartridge_access(user, cartridge)" in source
    assert "cartridge = await _scope_catalog_cartridge_arg(user, cartridge)" in source
    apps_scope_source = _read(ROOT / "console/app/domains/apps/scope.py")
    assert "no cartridge installed for this workspace" in apps_scope_source
    assert "return _empty_catalog_payload()" in source


def test_freshness_viewer_rejects_uninstalled_cartridges_and_uses_scope():
    source = _read(FRESHNESS)

    assert "def _require_cartridge" in source
    assert "cartridge not allowed for active workspace" in source
    assert "def _watermark_scope_for_user" in source
    assert "ew.watermark_scope = $" in source
    assert "er_run.tenant_id =" in source
    assert "er_run.workspace_id =" in source
    assert "scoped_db_for_user(pool, user)" in source
    assert "_require_cartridge(user, cartridge)" in source


def test_rag_and_watermark_migration_enables_rls_and_orphans_legacy_rows():
    source = _read(MIGRATION)

    assert "ALTER TABLE rag_sources" in source
    assert "ADD COLUMN IF NOT EXISTS tenant_id" in source
    assert "visibility = 'orphaned_legacy'" in source
    assert "ALTER TABLE rag_sources FORCE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE rag_chunks FORCE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE entity_watermarks FORCE ROW LEVEL SECURITY" in source
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in source
