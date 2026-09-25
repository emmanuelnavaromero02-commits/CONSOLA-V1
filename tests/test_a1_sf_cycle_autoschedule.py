from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "99zzzz_sap_successfactors_cycle_autoschedule.sql"
A2_MIGRATION = (
    REPO_ROOT / "infra" / "init" / "99zzzza_sap_successfactors_cycle_config.sql"
)
BOOTSTRAP = REPO_ROOT / "infra" / "init_dev" / "15_local_dev_bootstrap.sql"
EXTRACT_ALL = (
    REPO_ROOT / "cartridges" / "sap_successfactors" / "dags"
    / "sap_successfactors_extract_all.py"
)
CATALOG = (
    REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "services"
    / "catalog_service.py"
)
PRESENTER = (
    REPO_ROOT / "console-next" / "src" / "lib" / "control-room"
    / "experience-presenter.ts"
)
PAGE = (
    REPO_ROOT / "console-next" / "src" / "components" / "control-room"
    / "experience" / "ControlRoomExperiencePage.tsx"
)


def test_migration_schedules_the_cycle_dag_not_per_entity_extract():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "seed_sap_successfactors_cycle_schedule" in sql
    assert "'__foundation_cycle__'" in sql
    assert "'sap_successfactors_extract_all'" in sql
    assert "'scheduled'" in sql
    assert "'*/15 * * * *'" in sql
    assert '{"target": "foundation"}' in sql
    assert "99zzzz_sap_successfactors_cycle_autoschedule.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert MIGRATION.name > "99zzz_workspace_decision_idempotency.sql", (
        "must sort after F-SEG's 99zzz migration on fresh installs"
    )


def test_migration_resolves_scope_from_server_state_never_hardcoded():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(
        r"'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'", sql
    ), "no hardcoded tenant/workspace UUIDs"
    assert "cartridge_installations" in sql
    assert "'Default Tenant'" in sql and "'Main Workspace'" in sql
    assert "RETURN 0" in sql


def test_foundation_templates_bound_only_under_local_convention():
    sql = MIGRATION.read_text(encoding="utf-8")
    guard = sql.find("IF COALESCE(local_scope, FALSE) THEN")
    update = sql.find("SET connection_id = 'default'")
    assert guard != -1 and update != -1 and guard < update, (
        "per-entity retarget must sit behind the local-convention guard"
    )
    for entity in ("PerPerson", "EmpJob", "FOCompany", "Position"):
        assert f"'{entity}'" in sql
    update_block = sql[update:sql.index("END IF;", update)]
    assert "trigger_type" not in update_block, (
        "per-entity rows stay manual; only the cycle marker schedules"
    )


def test_dev_bootstrap_reseeds_after_workspace_exists():
    sql = BOOTSTRAP.read_text(encoding="utf-8")
    call = sql.find("seed_sap_successfactors_cycle_schedule")
    installs = sql.find("INSERT INTO cartridge_installations")
    assert call != -1, "bootstrap must call the cycle seeding function"
    assert installs != -1 and installs < call, "seeding must run after installations"
    assert "to_regproc" in sql, "guarded: bootstrap may run against older schemas"


def test_plan_skips_pseudo_entities():
    src = CATALOG.read_text(encoding="utf-8")
    assert re.search(
        r'if entity\.startswith\("__"\):\s*\n(?:\s*#.*\n)*\s*continue', src
    ), "get_extract_all_plan must skip '__'-prefixed marker rows"


def test_cycle_transitions_land_in_pipeline_runs():
    src = EXTRACT_ALL.read_text(encoding="utf-8")
    assert '"transition": "gold_materialized"' in src
    assert '"transition": "anomalies_detected"' in src
    assert 'entity="gold_foundation"' in src
    assert 'entity="employee_central_anomalies"' in src
    assert '"surface": "control_room.employee_central"' in src
    gold_call = src.find('entity="gold_foundation"')
    gold_refresh = src.find("trigger_successfactors_gold_refresh(")
    assert gold_refresh != -1 and gold_refresh < gold_call, (
        "transitions must be recorded after the gold refresh runs"
    )


def test_scheduler_fired_cycle_builds_signed_admission_context():
    src = EXTRACT_ALL.read_text(encoding="utf-8")
    fn = re.search(
        r"def authorize_refresh_chain\(.*?\n(?=\s*@task)", src, re.DOTALL
    )
    assert fn, "authorize_refresh_chain not found"
    body = fn.group(0)
    assert "_sign_security_context(" in body, "fallback must be signed"
    assert '"pipelines.run"' in body, (
        "admission demands the pipelines.run permission"
    )
    assert body.index("_sign_security_context(") < body.index(
        "refresh chain admission authority is required"
    ), "fallback must run before the fail-closed check"


def test_vault_reveal_fields_are_flattened():
    src = (
        REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "core"
        / "vault_client.py"
    ).read_text(encoding="utf-8")
    fn_src = re.search(
        r"def _flatten_connection_fields\(.*?\n(?=\ndef )", src, re.DOTALL
    )
    assert fn_src, "_flatten_connection_fields not found"
    namespace: dict = {}
    exec(fn_src.group(0), {"Any": object}, namespace)  # noqa: S102
    _flatten_connection_fields = namespace["_flatten_connection_fields"]

    flat = _flatten_connection_fields(
        {
            "conn_id": "default",
            "fields": {"base_url": "https://sf", "auth_method": "saml", "conn_id": "evil"},
        }
    )
    assert flat["base_url"] == "https://sf"
    assert flat["auth_method"] == "saml"
    assert flat["conn_id"] == "default", "identity keys must win on collision"
    untouched = {"conn_id": "x", "base_url": "flat"}
    assert _flatten_connection_fields(dict(untouched)) == untouched


def test_airflow_materializes_via_runtime_envelope():
    src = (
        REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "core"
        / "refinement_triggers.py"
    ).read_text(encoding="utf-8")
    assert "def _runtime_materialize_request(" in src
    assert "build_materialize_context" in src
    call_sites = src.count("await _runtime_materialize_request(")
    assert call_sites >= 3, (
        "silver refresh plus both gold-sweep loops must use the runtime "
        f"route under airflow (found {call_sites})"
    )
    assert 'internal_service == "airflow"' in src
    assert "/refresh-by-source" in src and "/datasets/" in src, (
        "the cartridge-container v1 paths must survive"
    )


def test_control_room_shows_freshness():
    presenter = PRESENTER.read_text(encoding="utf-8")
    assert "latestObservedAt" in presenter
    assert "formatRelativeFromNow" in presenter
    page = PAGE.read_text(encoding="utf-8")
    assert "Datos actualizados" in page
    assert "latestObservedAt(experience)" in page


def _a2_sql() -> str:
    return A2_MIGRATION.read_text(encoding="utf-8")


def _reconciler_body(sql: str) -> str:
    start = sql.index("CREATE OR REPLACE FUNCTION public.seed_sap_successfactors_cycle_schedule")
    return sql[start:sql.index("$$;", start)]


def test_a2_marker_and_templates_bound_to_config_connection():
    body = _reconciler_body(_a2_sql())
    bind = body[body.index("UPDATE public.entity_config"):]
    assert "connection_id = cfg.connection_id" in bind
    assert "tenant_id     = cfg.tenant_id" in bind
    assert "cfg.cron_expression" in bind
    assert "jsonb_build_object('target', cfg.target)" in bind
    assert "'default'" not in bind, "binding paths must never hardcode a connection"
    for entity in ("User", "PerPerson", "EmpJob", "FOCompany", "Position"):
        assert f"'{entity}'" in bind


def test_a2_missing_credential_disables_the_cycle():
    body = _reconciler_body(_a2_sql())
    gate = body.index("IF NOT has_credential")
    disabled = body.index("esperando credencial")
    ret = body.index("RETURN 2")
    bind = body.index("UPDATE public.entity_config")
    assert gate < disabled < ret < bind, (
        "credential gate must disable and return before the template binding"
    )
    assert "enabled         = FALSE" in body[gate:ret]
    assert "vault_entries" in body
    assert "v.scope = 'connections'" in body
    assert "v.key = cfg.connection_id" in body


def test_a2_cadence_comes_from_config_not_a_literal():
    sql = _a2_sql()
    body = _reconciler_body(sql)
    marker = body[body.index("'__foundation_cycle__'"):]
    assert "cfg.cron_expression" in marker
    dev_fallback = body[body.index("No managed cycle"):body.index("SELECT TRUE INTO dev_scope")]
    allowed = dev_fallback.count("'*/15 * * * *'")
    assert body.count("'*/15 * * * *'") == allowed, (
        "cron literals outside the dev fallback would override the config"
    )


def test_a2_dev_path_unchanged():
    sql = _a2_sql()
    body = _reconciler_body(sql)
    assert "'Default Tenant'" in body and "'Main Workspace'" in body
    assert "cfg.connection_id = 'default'" in body, "dev carve-out must be explicit"
    assert "cartridge_installations" not in body, (
        "the A1 'any ready installation -> default cycle' path must be gone"
    )
    boot = BOOTSTRAP.read_text(encoding="utf-8")
    assert "cartridge_cycle_config" in boot
    insert = boot.index("INSERT INTO public.cartridge_cycle_config")
    perform = boot.index("PERFORM public.seed_sap_successfactors_cycle_schedule()")
    assert insert < perform, "bootstrap must seed the config row before reconciling"
    assert "NOT EXISTS" in boot[insert:perform], (
        "bootstrap must never fight an already-active cycle config"
    )


def test_a2_only_foundation_templates_are_rebound():
    body = _reconciler_body(_a2_sql())
    bind = body[body.index("UPDATE public.entity_config"):]
    for femsa_only in ("PerEmail", "PaymentInformationDetailV3", "EmpEmploymentTermination"):
        assert f"'{femsa_only}'" not in bind
    assert bind.count("UPDATE public.entity_config") == 1
    assert "trigger_type" not in bind[:bind.index("INSERT INTO public.entity_config")], (
        "per-entity rows stay manual; only the marker schedules"
    )


def test_a2_single_active_cycle_is_a_hard_constraint():
    sql = _a2_sql()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS cartridge_cycle_config_single_active" in sql
    assert "WHERE enabled" in sql


def test_a2_rls_matches_house_boundaries():
    sql = _a2_sql()
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "REVOKE ALL ON public.cartridge_cycle_config FROM PUBLIC" in sql


def test_a2_migration_sorts_after_a1():
    assert A2_MIGRATION.name > MIGRATION.name
    assert A2_MIGRATION.name > "99zzz_workspace_decision_idempotency.sql"
    sql = _a2_sql()
    assert "99zzzza_sap_successfactors_cycle_config.sql" in sql
    assert "INSERT INTO schema_migrations" in sql


E1_MIGRATION = (
    REPO_ROOT / "infra" / "init" / "99zzzzc_sap_successfactors_cycle_target_all.sql"
)
TRIGGERS = (
    REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "core"
    / "refinement_triggers.py"
)
ORDERS = (
    REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config"
    / "gold_dataset_orders.json"
)


def _e1_sql() -> str:
    return E1_MIGRATION.read_text(encoding="utf-8")


def test_e1_cycle_target_defaults_to_all():
    sql = _e1_sql()
    assert "ALTER COLUMN target SET DEFAULT 'all'" in sql
    assert "'*/15 * * * *', 'all', TRUE" in sql


def test_e1_existing_foundation_configs_promoted_but_explicit_targets_kept():
    sql = _e1_sql()
    update = sql[sql.index("UPDATE public.cartridge_cycle_config"):]
    update = update[: update.index(";")]
    assert "SET target = 'all'" in update
    assert "cartridge_id = 'sap_successfactors'" in update
    assert "target = 'foundation'" in update, (
        "the promotion must be guarded to rows still on the old default"
    )


def test_e1_reconciler_replaced_and_rerun_forward_only():
    sql = _e1_sql()
    assert "CREATE OR REPLACE FUNCTION public.seed_sap_successfactors_cycle_schedule()" in sql
    assert "SELECT public.seed_sap_successfactors_cycle_schedule();" in sql
    assert "jsonb_build_object('target', cfg.target)" in sql, (
        "the marker keeps taking its target from config, never a literal"
    )
    assert E1_MIGRATION.name > "99zzzzb_control_room_lessons_honest_confidence.sql"
    assert "99zzzzc_sap_successfactors_cycle_target_all.sql" in sql
    assert "INSERT INTO schema_migrations" in sql


def test_e1_credential_gate_and_scope_resolution_survive_the_replace():
    sql = _e1_sql()
    assert "RETURN 2" in sql
    assert "vault_entries" in sql
    assert "'Default Tenant'" in sql and "'Main Workspace'" in sql
    assert not re.search(
        r"'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'", sql
    ), "no hardcoded tenant/workspace UUIDs"
    for entity in ("'User'", "'PerPerson'", "'FOCompany'", "'Position'"):
        assert entity in sql


def test_e1_target_all_reaches_the_talent_cascade():
    import json as _json

    src = TRIGGERS.read_text(encoding="utf-8")
    assert "SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) + list(SUCCESSFACTORS_GOLD_TALENT_ORDER)" in src
    assert "SUCCESSFACTORS_SILVER_TALENT_CURATED_ORDER" in src

    orders = _json.loads(ORDERS.read_text(encoding="utf-8"))
    talent = orders["talent_order"]
    for needed in (
        "sap_successfactors_talent_cpa_scores",
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
        "sap_successfactors_talent_9box_operational",
        "sap_successfactors_talent_retention_risk",
    ):
        assert needed in talent, f"{needed} must be part of the autonomous cycle"
    assert orders["silver_talent_curated_order"], (
        "curated talent silver must not be empty for target=all"
    )


def test_e1_nine_box_stays_fail_closed_no_proxies():
    nine = (
        REPO_ROOT / "cartridges" / "sap_successfactors" / "datasets"
        / "sap_successfactors_talent_9box.sql"
    ).read_text(encoding="utf-8")
    assert "no fabrica proxies" in nine or "insufficient_data" in nine
    cpa = (
        REPO_ROOT / "cartridges" / "sap_successfactors" / "datasets"
        / "sap_successfactors_talent_cpa_scores.sql"
    ).read_text(encoding="utf-8")
    assert "insufficient_data" in cpa


E2_MIGRATION = (
    REPO_ROOT / "infra" / "init"
    / "99zzzzd_sap_successfactors_cycle_talent_rebind.sql"
)


def _e2_sql() -> str:
    return E2_MIGRATION.read_text(encoding="utf-8")


def test_e2_reconciler_rebinds_talent_cpa_entities_to_active_config():
    sql = _e2_sql()
    assert sql.count("UPDATE public.entity_config ec") == 2
    for entity in (
        "'PerformanceReview'", "'UserSkill'", "'CompetencyEntity'",
        "'DevGoal'", "'SkillProfile'", "'WorkerCompetencyAssessment'",
    ):
        assert entity in sql, entity
    talent_block = sql.split("'PerformanceReview'", 1)[0].rsplit(
        "UPDATE public.entity_config ec", 1
    )[1] + sql.split("'PerformanceReview'", 1)[1].split(";", 1)[0]
    assert "SET connection_id = cfg.connection_id" in sql


def test_e2_does_not_touch_other_client_entities():
    sql = _e2_sql()
    for forbidden in (
        "'EmpCompensation'", "'EmpEmploymentTermination'", "'SuccessionNomination'",
        "'LearningAssignment'", "'PaymentInformationDetailV3'",
    ):
        assert forbidden not in sql, f"no debe reatar {forbidden}"


def test_e2_forward_only_and_registers():
    sql = _e2_sql()
    assert "CREATE OR REPLACE FUNCTION public.seed_sap_successfactors_cycle_schedule()" in sql
    assert "SELECT public.seed_sap_successfactors_cycle_schedule();" in sql
    assert E2_MIGRATION.name > "99zzzzc_sap_successfactors_cycle_target_all.sql"
    assert "99zzzzd_sap_successfactors_cycle_talent_rebind.sql" in sql
    assert "INSERT INTO schema_migrations" in sql
    assert "RETURN 2" in sql and "vault_entries" in sql
