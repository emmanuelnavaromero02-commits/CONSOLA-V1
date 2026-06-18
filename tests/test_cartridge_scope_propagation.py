from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCOPED_CARTRIDGES = (
    "replicon",
    "hubspot",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "salesforce",
)
SAP_CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors")


def _read(path: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8")
    ast.parse(source)
    return source


def _function_args(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == name
        ):
            return {arg.arg for arg in node.args.args + node.args.kwonlyargs}
    raise AssertionError(f"function not found: {name}")


def test_console_entity_run_forwards_backend_security_context_to_cartridge_skills():
    source = _read("console/app/routers/cartridges.py")

    assert "security_context = build_security_context(user)" in source
    assert 'params={"conn_id": selected_conn_id} if selected_conn_id else None' in source
    assert 'json={"security_context": security_context}' in source


def test_skills_preserve_forwarded_workspace_scope():
    for cartridge in SCOPED_CARTRIDGES:
        source = _read(f"cartridges/{cartridge}/app/api/routes_skills.py")

        assert "Body(None)" in source
        assert "def _security_context(" in source
        assert "token = _set_security_context(ctx)" in source
        assert 'scoped_config = {**config, "security_context": ctx}' in source
        if cartridge in {"replicon", "sap_successfactors"}:
            assert 'scoped_config = {**scoped_config, "conn_id": conn_id}' in source
        assert "def _run_kb_with_context(" in source
        assert "return run_knowledge_bit(kb_id, ctx)" in source
        assert "return run_all_knowledge_bits(ctx)" in source
        assert "reset_security_context(token)" in source


def test_sap_console_extract_routes_preserve_forwarded_workspace_scope():
    for cartridge in SAP_CARTRIDGES:
        source = _read(f"cartridges/{cartridge}/app/api/routes_console.py")

        assert "Body(None)" in source
        assert "def _security_context(" in source
        assert "token = _set_security_context(ctx)" in source
        assert 'return {**config, "security_context": ctx} if ctx else config' in source
        assert "_mark_external_job(_trigger_silver_refresh, entity_id, ctx)" in source
        assert "reset_security_context(token)" in source


def test_salesforce_console_extract_routes_preserve_forwarded_workspace_scope():
    source = _read("cartridges/salesforce/app/api/routes_console.py")

    assert "Body(None)" in source
    assert "def _security_context(" in source
    assert "token = _set_security_context(ctx)" in source
    assert 'return {**config, "security_context": ctx} if ctx else config' in source
    assert "_mark_external_job(_trigger_silver_refresh, entity_id, ctx)" in source
    assert "reset_security_context(token)" in source


def test_cartridge_job_runners_pass_scope_to_airflow_conf():
    for cartridge in SCOPED_CARTRIDGES:
        source = _read(f"cartridges/{cartridge}/app/core/job_runner.py")

        assert 'conf["security_context"] = security_context' in source
        assert 'conf["tenant_id"] = security_context["tenant_id"]' in source
        assert 'conf["workspace_id"] = security_context["workspace_id"]' in source
        if cartridge in {"replicon", "sap_successfactors"}:
            assert 'conf["conn_id"] = conn_id' in source


def test_airflow_dags_forward_scope_to_raw_writes_and_skill_calls():
    replicon = _read("cartridges/replicon/dags/replicon_extract.py")
    hubspot = _read("cartridges/hubspot/dags/hubspot_extract.py")
    hubspot_extract_all = _read("cartridges/hubspot/dags/hubspot_extract_all.py")
    salesforce = _read("cartridges/salesforce/dags/salesforce_extract.py")
    salesforce_extract_all = _read(
        "cartridges/salesforce/dags/salesforce_extract_all.py"
    )

    assert "def _scope_prefix(" in replicon
    assert 'return f"tenant_id={tenant}/workspace_id={workspace}/"' in replicon
    assert "_upload_parquet(df, entity, run_id, tenant_id, workspace_id)" in replicon
    assert 'key = f"raw/replicon/{entity}/{scope}load_date=' in replicon
    assert 'conf.get("conn_id") or conf.get("connection_id") or DEFAULT_CONN_ID' in replicon
    assert "skill_body = {" in hubspot
    assert 'for key in ("tenant_id", "workspace_id", "security_context")' in hubspot
    assert "json=skill_body" in hubspot
    assert "skill_body = {" in hubspot_extract_all
    assert (
        'for key in ("tenant_id", "workspace_id", "security_context")'
        in hubspot_extract_all
    )
    assert "json=skill_body" in hubspot_extract_all
    assert "skill_body = {" in salesforce
    assert 'for key in ("tenant_id", "workspace_id", "security_context")' in salesforce
    assert "json=skill_body" in salesforce
    assert "skill_body = {" in salesforce_extract_all
    assert (
        'for key in ("tenant_id", "workspace_id", "security_context")'
        in salesforce_extract_all
    )
    assert "json=skill_body" in salesforce_extract_all

    for cartridge in SAP_CARTRIDGES:
        source = _read(f"cartridges/{cartridge}/dags/{cartridge}_extract.py")
        assert "skill_body = {" in source
        if cartridge == "sap_successfactors":
            assert "def _security_context_from_conf(" in source
            assert '"conn_id": conf.get("conn_id") or conf.get("connection_id") or None' in source
        else:
            assert 'for key in ("tenant_id", "workspace_id", "security_context")' in source
        assert "json=skill_body" in source
        extract_all = _read(f"cartridges/{cartridge}/dags/{cartridge}_extract_all.py")
        assert "skill_body = {" in extract_all
        if cartridge == "sap_successfactors":
            assert "def _security_context_from_conf(" in extract_all
            assert '"conn_id": conf.get("conn_id") or conf.get("connection_id") or None' in extract_all
        else:
            assert (
                'for key in ("tenant_id", "workspace_id", "security_context")'
                in extract_all
            )
        assert "json=skill_body" in extract_all


def test_entity_scheduler_forwards_scope_and_connection_id_to_scheduled_dags():
    source = _read("airflow/dags/entity_scheduler.py")

    assert "ec.tenant_id::text AS tenant_id" in source
    assert "ec.workspace_id::text AS workspace_id" in source
    assert "ec.connection_id" in source
    assert 'conf["tenant_id"] = it["tenant_id"]' in source
    assert 'conf["workspace_id"] = it["workspace_id"]' in source
    assert 'conf["conn_id"] = it["conn_id"]' in source


def test_scoped_allowed_prefixes_are_emitted_for_all_live_cartridges():
    for cartridge in SCOPED_CARTRIDGES:
        source = _read(f"cartridges/{cartridge}/app/core/request_context.py")
        assert 'scope = f"tenant_id={tenant}/workspace_id={workspace}/"' in source
        assert (
            f'f"raw/{{CARTRIDGE_ID}}/{{scope}}"' in source
            or f'f"raw/{cartridge}/{{scope}}"' in source
        )
        assert (
            "allowed_prefixes = allowed_prefixes" in source
            or '"allowed_prefixes": allowed_prefixes' in source
        )


def test_knowledge_bits_read_write_under_forwarded_workspace_scope():
    for cartridge in SCOPED_CARTRIDGES:
        kb_service = _read(f"cartridges/{cartridge}/app/services/kb_service.py")
        duckdb_service = _read(f"cartridges/{cartridge}/app/services/duckdb_service.py")

        assert "def _scope_kb_sql(" in kb_service
        assert "resolved_sql = _scope_kb_sql(sql, security_context)" in kb_service
        assert (
            "write_kb_parquet(df, output_path, kb_id, run_id, security_context)"
            in kb_service
        )
        assert "write_kb_to_postgres(df, pg_table, security_context)" in kb_service
        if cartridge != "salesforce":
            assert "require_tenant_workspace_scope(security_context)" in duckdb_service
            assert "_path_has_scope(output_path, scope)" in duckdb_service
        assert "tenant_id=:tenant_id AND workspace_id=:workspace_id" in duckdb_service


def test_mcp_infra_injects_trusted_scope_before_cartridge_execution():
    source = _read("mcp-infra/app/main.py")
    tools = _read("mcp-infra/app/tools/cartridges.py")

    assert "def _inject_cartridge_execution_scope(" in source
    assert 'args["security_context"] = ctx' in source
    assert "def _reject_client_owned_scope_args(" in source
    assert '"backend-owned arg is not allowed' in source
    assert "_inject_cartridge_execution_scope(ctx, args)" in source
    assert "_reject_client_owned_scope_args(args)" in source
    assert 'conf["security_context"] = ctx' in source
    assert "def _attach_security_scope(" in tools
    for public_arg in ('"tenant_id": {"type": "string"}', '"workspace_id": {"type": "string"}', '"security_context": {"type": "object"}'):
        assert public_arg not in tools
    assert "security_context: dict[str, Any] | None = None" in tools
    assert "_scoped_object_prefix(" in tools
    assert "_scope_cartridge_sql(" in tools
    assert "_scoped_rag_source_name(" in tools

    for fn_name in (
        "cartridge_sync_semantic_to_rag",
        "cartridge_list_entities",
        "cartridge_extract",
        "cartridge_extract_all",
        "cartridge_run_kb",
    ):
        args = _function_args(tools, fn_name)
        assert "security_context" in args
        assert "tenant_id" not in args
        assert "workspace_id" not in args


def test_direct_scoped_cartridge_mcp_invokes_load_forwarded_context():
    for cartridge in (
        "hubspot",
        "replicon",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "salesforce",
    ):
        source = _read(f"cartridges/{cartridge}/app/main.py")
        assert (
            'set_security_context(body.get("security_context"))' in source
            or "body.get(\"security_context\")," in source
            or "x-security-context" in source
        )
        assert "reset_security_context(token)" in source


def test_mcp_admin_sql_sensitive_table_denylist_covers_tenant_and_vault_tables():
    source = _read("mcp-infra/app/main.py")

    for table in (
        "tenants",
        "workspaces",
        "roles",
        "user_workspace_roles",
        "decisions",
        "decision_actions",
        "vault_access_log",
        "vault_entries",
    ):
        assert f'"{table}"' in source
