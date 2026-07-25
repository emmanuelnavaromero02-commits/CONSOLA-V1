from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_intelligence_migration_is_workspace_scoped_and_self_registered():
    migration = read("infra/init/98_intelligence_engine.sql")
    for table in (
        "metric_baselines",
        "intelligence_signals",
        "evidence_packs",
        "evidence_items",
        "hypotheses",
        "decision_options",
        "prediction_outcomes",
    ):
        table_block = migration.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[
            1
        ].split(");", 1)[0]
        assert "tenant_id" in table_block
        assert "workspace_id" in table_block
    assert "98_intelligence_engine.sql" in migration
    assert "control_room_items" not in migration


def test_intelligence_external_predictive_migration_is_workspace_scoped():
    migration = read("infra/init/99_intelligence_external_predictive.sql")
    assert "ADD COLUMN IF NOT EXISTS prediction_horizon_days" in migration
    assert "ADD COLUMN IF NOT EXISTS predicted_value" in migration
    for table in ("external_intelligence_sources", "external_evidence_cache"):
        table_block = migration.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[
            1
        ].split(");", 1)[0]
        assert "tenant_id" in table_block
        assert "workspace_id" in table_block
    assert "99_intelligence_external_predictive.sql" in migration


def test_intelligence_native_rls_migration_is_fail_closed():
    migration = read("infra/init/99d_intelligence_native_rls.sql")
    for table in (
        "metric_baselines",
        "intelligence_signals",
        "evidence_packs",
        "evidence_items",
        "hypotheses",
        "decision_options",
        "prediction_outcomes",
        "external_intelligence_sources",
        "external_evidence_cache",
    ):
        assert f"'{table}'" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "current_setting(''app.workspace_id'', true)" in migration
    assert "WITH CHECK" in migration


def test_priority_cartridges_have_valid_intelligence_contracts():
    for cartridge_id in ("hubspot", "replicon", "salesforce", "sap_hcm"):
        path = ROOT / "cartridges" / cartridge_id / "app/config/intelligence.yaml"
        packaged = (
            ROOT / "console/app/config/intelligence_contracts" / f"{cartridge_id}.yaml"
        )
        assert path.exists(), f"missing intelligence contract for {cartridge_id}"
        assert (
            packaged.exists()
        ), f"missing packaged console intelligence contract for {cartridge_id}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        packaged_data = yaml.safe_load(packaged.read_text(encoding="utf-8"))
        assert data["cartridge"] == cartridge_id
        assert packaged_data == data
        assert data.get("external_sources"), f"no external sources in {path}"
        assert data["metrics"], f"no metrics in {path}"
        for metric in data["metrics"]:
            assert metric["id"]
            assert metric["dataset"]
            assert metric["entity"]["id_field"]
            assert metric["time_field"]
            assert metric["value_field"]
            assert metric["baseline"]["method"] == "moving_average"
            assert metric["baseline"]["minimum_history"] >= 2
            assert metric["prediction"]["horizon_days"]
            assert metric["impact"]["currency"]
            assert metric["hypotheses"]
            assert metric["action_templates"]
            if cartridge_id == "replicon" and metric["id"] in {
                "project_margin",
                "billable_hours",
            }:
                template = metric.get("simulation_template")
                assert template, f"missing simulation_template for {metric['id']}"
                assert template["output_metric"] == "net_value"
                assert template["input_variables"]["baseline_value"]["value"] == (
                    "$signal.expected_value"
                )
                assert template["input_variables"]["expected_delta"]["mean"] == (
                    "$signal.deviation_value"
                )


def test_intelligence_contract_loader_reads_packaged_contracts():
    from console.app.services.intelligence.contracts import load_contracts

    contracts = load_contracts({"hubspot", "replicon", "salesforce", "sap_hcm"})
    loaded = {contract["cartridge"] for contract in contracts}
    assert {"hubspot", "replicon", "salesforce", "sap_hcm"} <= loaded


def test_intelligence_router_is_registered_and_mutations_are_guarded():
    main = read("console/app/main.py")
    router = read("console/app/routers/intelligence.py")
    assert "intelligence_router" in main
    assert "app.include_router(intelligence_router.router)" in main
    assert "app.include_router(intelligence_router.v1_router)" in main
    assert "app.include_router(intelligence_router.internal_router)" in main
    assert 'APIRouter(prefix="/api/v1/intelligence"' in router
    assert 'APIRouter(prefix="/internal/intelligence"' in router
    assert "BaseModel" in router
    assert 'extra="forbid"' in router
    assert "cartridge_id" in router
    assert "metrics" in router
    assert '@router.get("/signals"' in router
    assert '@v1_router.get("/signals"' in router
    assert '@router.get("/readiness"' in router
    assert '"/runs/{run_id}"' in router
    assert '@router.get("/runs"' in router
    assert '@router.get("/history"' in router
    assert '@router.get("/calibration"' in router
    assert '"gold_refresh"' in router
    assert '"/gold-refresh"' in router
    assert "verify_internal_api_key" in router
    assert "only airflow can trigger Gold refresh intelligence" in router
    assert "gold-refresh:" in router
    assert '@router.get("/external/sources"' in router
    assert '"/external/sources/{source_id}"' in router
    assert '"/external/run"' in router
    assert "@router.post(" in router
    assert 'Depends(require_permission("datasets.read"))' in router
    assert 'Depends(require_permission("control_room.write"))' in router
    assert "Depends(require_csrf)" in router
    assert "def _invalidate_control_room_cache" in router
    assert "_invalidate_control_room_cache(user)" in router


def test_gold_refresh_intelligence_migration_extends_run_mode_safely():
    migration = read("infra/init/99y_gold_refresh_intelligence.sql")
    assert "DROP CONSTRAINT IF EXISTS intelligence_runs_mode_chk" in migration
    assert "ADD CONSTRAINT intelligence_runs_mode_chk" in migration
    assert "'gold_refresh'" in migration
    assert "99y_gold_refresh_intelligence.sql" in migration


def test_dataset_refresh_chain_notifies_console_after_pipeline_save_only_for_gold_ready():
    source = read("airflow/dags/dataset_refresh_chain.py")
    assert "CONSOLE_INTERNAL_URL" in source
    assert "CONSOLE_URL" in source
    assert "INTERNAL_API_KEY_AIRFLOW_TO_{target}" in source
    assert "/internal/intelligence/gold-refresh" in source
    assert "pipeline_run_id = f\"dataset_refresh_chain:{ctx['run_id']}\"" in source
    assert "_successful_materialized_datasets(results)" in source
    assert 'status == "success" or (status == "partial" and allow_partial)' in source
    pipeline_save_pos = source.index('"tool": "pipeline_run_save"')
    trigger_pos = source.rindex("_trigger_gold_refresh_intelligence(")
    assert pipeline_save_pos < trigger_pos


def test_scope_owner_hotfix_migration_covers_vault_and_intelligence_owner():
    migration = read("infra/init/99g_scope_owner_hotfix.sql")
    for table in (
        "intelligence_signals",
        "evidence_packs",
        "evidence_items",
        "hypotheses",
        "decision_options",
        "prediction_outcomes",
        "control_room_items",
    ):
        assert f"'{table}'" in migration
    assert "owner_user_id" in migration
    assert "vault_entries_tenant_workspace_rls" in migration
    assert "vault_access_log_tenant_workspace_rls" in migration
    assert "regexp_match(cartridge" in migration
    assert "regexp_match(key" in migration


def test_intelligence_employee_owner_filters_are_enforced():
    persistence = read("console/app/services/intelligence/persistence.py")
    for function_name in (
        "list_signals",
        "get_signal",
        "select_option",
        "record_outcome",
        "_latest_evidence_pack",
    ):
        block = persistence.split(f"async def {function_name}", 1)[1].split(
            "\nasync def ", 1
        )[0]
        assert "_can_read_workspace_wide(user)" in block
        assert "_owner_user_id(user)" in block
        assert "owner_user_id" in block
        assert "not can_read_all" in block


def test_pipeline_run_save_rejects_missing_scope_before_db_insert():
    source = read("mcp-infra/app/tools/pipeline.py")
    block = source.split("def pipeline_run_save(", 1)[1].split(
        "\n\n\n# ── DAG source storage", 1
    )[0]
    assert "pipeline_run_save requires tenant_id and workspace_id" in block
    assert "raise HTTPException(403" in block
    scope_pos = block.index("_set_db_scope(cur, tenant_id, workspace_id)")
    guard_pos = block.index("pipeline_run_save requires tenant_id and workspace_id")
    insert_pos = block.index("INSERT INTO pipeline_runs")
    assert scope_pos < guard_pos < insert_pos


def test_mcp_pipeline_scope_guard_has_no_unscoped_admin_bypass():
    source = read("mcp-infra/app/main.py")
    guard = source.split("def _validate_pipeline_run_save_scope", 1)[1].split(
        "\ndef _require_dag_registered", 1
    )[0]
    assert 'if cartridge_id == "platform":' in guard
    assert "_is_unscoped_admin_context" not in guard
    assert "pipeline run tenant/workspace scope is required" in guard


def test_scope_hardening_migration_only_allows_platform_global_pipeline_runs():
    migration = read("infra/init/99h_scope_hardening.sql")
    assert "pipeline_runs_platform_global_rls" in migration
    assert "cartridge_id = ''platform''" in migration
    assert "tenant_id IS NULL" in migration
    assert "workspace_id IS NULL" in migration


def test_vault_legacy_scope_classification_blocks_global_connections():
    migration = read("infra/init/99i_vault_legacy_scope_classification.sql")
    assert "vault_legacy_unscoped_entries" in migration
    assert "legacy_global_connection_requires_workspace_migration" in migration
    assert "DROP POLICY IF EXISTS vault_entries_global_legacy_rls" in migration
    assert "CREATE POLICY vault_entries_platform_global_rls" in migration
    policy = migration.split("CREATE POLICY vault_entries_platform_global_rls", 1)[1]
    assert "scope = 'destinations' AND cartridge = 'platform'" in policy
    assert "scope = 'secrets'" in policy
    assert "scope = 'connections'" not in policy


def test_console_vault_proxy_forwards_signed_security_context():
    source = read("console/app/main.py")
    v1_source = read("console/app/routers/v1/vault.py")
    assert "def _vault_headers_for_user" in source
    compact = "".join(source.split())
    assert '"x-security-context":json.dumps(build_security_context(user)' in compact
    vault_section = source.split("# ── Vault proxy", 1)[1].split("# ── RAG proxy", 1)[0]
    assert "_tenant_vault_conn_id" in vault_section
    assert 'f"{prefix}{clean}"' not in vault_section
    assert "headers=_vault_headers_for_user(user)" in vault_section
    assert 'headers=_hdr_for("VAULT")' not in v1_source
    assert "headers=_vault_headers_for_user(user)" in v1_source


def test_console_user_vault_calls_do_not_bypass_security_context():
    for path in (
        "console/app/main.py",
        "console/app/routers/studio.py",
        "console/app/routers/v1/vault.py",
        "console/app/routers/v1/pipeline_studio.py",
    ):
        source = read(path)
        assert 'headers=_hdr_for("VAULT")' not in source
        assert "x-security-context" in source or "_vault_headers_for_user" in source


def test_service_readiness_defaults_to_private_beta_golden_path():
    readiness = read("console/app/services/intelligence/readiness.py")
    assert (
        'os.environ.get("INTELLIGENCE_READINESS_CARTRIDGES", "hubspot,replicon")'
        in readiness
    )
