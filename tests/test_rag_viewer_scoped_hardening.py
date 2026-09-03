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
RAG_TOOLS = ROOT / "mcp-infra/app/tools/rag.py"
PIPELINE_TOOLS = ROOT / "mcp-infra/app/tools/pipeline.py"
CARTRIDGE_TOOLS = ROOT / "mcp-infra/app/tools/cartridges.py"
FRESHNESS = ROOT / "console/app/routers/freshness.py"
MIGRATION = ROOT / "infra/init/99n_rag_viewer_scoped_rls.sql"
GROUNDED_MIGRATION = ROOT / "infra/init/99zzzzg_control_room_grounded_analysis.sql"
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


def _load_cartridge_filter():
    source = _read(RAG_STORE)
    tree = __import__("ast").parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, __import__("ast").FunctionDef)
        and node.name == "_cartridge_filter"
    )
    module = __import__("ast").Module(body=[function], type_ignores=[])
    __import__("ast").fix_missing_locations(module)
    namespace: dict[str, object] = {}
    exec(compile(module, str(RAG_STORE), "exec"), namespace)
    return namespace["_cartridge_filter"]


def test_rag_cartridge_filter_enforces_signed_ceiling():
    cartridge_filter = _load_cartridge_filter()

    assert cartridge_filter(None, {"allowed_cartridges": ["sap_successfactors"]}) == [
        "sap_successfactors"
    ]
    assert cartridge_filter(
        ["sap_successfactors", "replicon"],
        {"allowed_cartridges": ["sap_successfactors"]},
    ) == ["sap_successfactors"]
    assert cartridge_filter(["replicon"], {"allowed_cartridges": ["sap_successfactors"]}) == [
        "__no_authorized_cartridge__"
    ]
    assert cartridge_filter(None, {"allowed_cartridges": []}) == [
        "__no_authorized_cartridge__"
    ]
    # Legacy internal maintenance calls do not carry a signed user scope.
    assert cartridge_filter(["replicon"], None) == ["replicon"]


def test_rag_cartridge_scope_is_persisted_and_exposed_end_to_end():
    migration = _read(GROUNDED_MIGRATION)
    store = _read(RAG_STORE)
    tools = _read(RAG_TOOLS)

    assert "ADD COLUMN IF NOT EXISTS cartridge_id" in migration
    assert "rag_sources_scope_cartridge_kind_idx" in migration
    assert "cartridge_id = ANY" in store
    assert "RAG cartridge is outside caller scope" in store
    assert '"cartridges"' in tools
    assert "cartridge_id=cartridge_id" in tools


def test_reserved_legacy_names_never_gain_cartridge_authority():
    migration = _read(GROUNDED_MIGRATION)
    store = _read(RAG_STORE)

    assert "Never infer authority from a legacy source name" in migration
    assert "SET cartridge_id = cartridge.id" not in migration
    assert "SET cartridge_id = dataset.cartridge" not in migration
    # An agent supplies an explicit cartridge filter.  That SQL admits only an
    # exact persisted cartridge_id and never the NULL legacy-document branch.
    assert 'where += f" AND s.cartridge_id = ANY(${len(params)})"' in store
    assert "elif cartridges:" in store


def test_mcp_rag_tools_receive_server_owned_scope():
    source = _read(MCP_MAIN)
    tools = _read(RAG_TOOLS)

    assert "def _require_rag_context_scope" in source
    assert "if tool in _RAG_READ_TOOLS | _RAG_WRITE_TOOLS" in source
    assert "_require_rag_context_scope(ctx)" in source
    assert 'args["security_context"] = ctx' in source
    assert "_rag_row_allowed" in source
    assert "RAG schema sources are server-managed" in source
    assert "RAG cartridge sources are server-managed" in source
    assert 'args["kind"] = "document"' in source
    assert 'args["cartridge_id"] = None' in source
    assert "RAG schema and cartridge sources are server-managed" in tools


def test_rag_source_prefixes_are_literal_not_sql_like_patterns():
    source = _read(RAG_STORE)

    assert source.count("starts_with(s.name, prefix.value)") == 2
    assert "s.name LIKE ANY" not in source
    assert '[f"{value}%" for value in source_prefixes]' not in source


def test_rag_delete_is_restricted_to_user_owned_workspace_documents():
    gateway = _read(MCP_MAIN)
    store = _read(RAG_STORE)

    assert "_rag_user_owned_document_allowed(ctx, source)" in gateway
    assert "user_owned_document_only=True" in gateway
    assert "user_owned_document_only" in store
    assert "AND kind = 'document'" in store
    assert "AND cartridge_id IS NULL" in store
    assert "AND visibility = 'workspace'" in store
    assert "AND NOT starts_with(name, 'raw:')" in store
    assert "AND NOT starts_with(name, 'dataset:')" in store
    assert "AND NOT starts_with(name, '_semantic_')" in store


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
    assert "_filter_technical_sources(user, sources)" in source
    assert "_require_technical_cartridge_access(user, cartridge)" in source
    assert "cartridge = await _scope_catalog_cartridge_arg(user, cartridge)" in source
    assert "no cartridge installed for this workspace" in source
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
