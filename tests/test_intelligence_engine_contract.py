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
        table_block = migration.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(");", 1)[0]
        assert "tenant_id" in table_block
        assert "workspace_id" in table_block
    assert "98_intelligence_engine.sql" in migration
    assert "control_room_items" not in migration


def test_intelligence_external_predictive_migration_is_workspace_scoped():
    migration = read("infra/init/99_intelligence_external_predictive.sql")
    assert "ADD COLUMN IF NOT EXISTS prediction_horizon_days" in migration
    assert "ADD COLUMN IF NOT EXISTS predicted_value" in migration
    for table in ("external_intelligence_sources", "external_evidence_cache"):
        table_block = migration.split(f"CREATE TABLE IF NOT EXISTS {table}", 1)[1].split(");", 1)[0]
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
        packaged = ROOT / "console/app/config/intelligence_contracts" / f"{cartridge_id}.yaml"
        assert path.exists(), f"missing intelligence contract for {cartridge_id}"
        assert packaged.exists(), f"missing packaged console intelligence contract for {cartridge_id}"
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
    assert 'APIRouter(prefix="/api/v1/intelligence"' in router
    assert "BaseModel" in router
    assert 'extra="forbid"' in router
    assert '@router.get("/signals"' in router
    assert '@v1_router.get("/signals"' in router
    assert '@router.get("/readiness"' in router
    assert '@router.get("/external/sources"' in router
    assert '"/external/sources/{source_id}"' in router
    assert '"/external/run"' in router
    assert '@router.post(' in router
    assert 'Depends(require_permission("datasets.read"))' in router
    assert 'Depends(require_permission("control_room.write"))' in router
    assert "Depends(require_csrf)" in router


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


def test_pipeline_run_save_rejects_missing_scope_before_db_insert():
    source = read("mcp-infra/app/tools/pipeline.py")
    assert "pipeline_run_save requires tenant_id and workspace_id" in source
    assert "raise HTTPException(403" in source
    insert_pos = source.index("INSERT INTO pipeline_runs")
    guard_pos = source.index("pipeline_run_save requires tenant_id and workspace_id")
    assert guard_pos < insert_pos


def test_console_vault_proxy_forwards_signed_security_context():
    source = read("console/app/main.py")
    assert "def _vault_headers_for_user" in source
    assert '"x-security-context": json.dumps(build_security_context(user)' in source
    vault_section = source.split("# ── Vault proxy", 1)[1].split("# ── RAG proxy", 1)[0]
    assert "_tenant_vault_conn_id" in vault_section
    assert 'f"{prefix}{clean}"' not in vault_section
    assert "headers=_vault_headers_for_user(user)" in vault_section


def test_control_room_surfaces_persisted_intelligence_items_and_ui_pack():
    service = read("console/app/services/control_room/api.py")
    state = read("console/app/services/control_room/state.py")
    ui = read("console-next/src/app/(shell)/control-room/page.tsx")
    assert "item_kind = 'intelligence_signal'" in service
    assert "_persisted_intelligence_items" in service
    assert '"intelligence": metadata.get("intelligence")' in state
    assert '"intelligence_signal"' in ui
    assert "function IntelligencePanel" in ui
    assert "Registrar outcome" in ui
    assert "Evidence Pack" in ui
