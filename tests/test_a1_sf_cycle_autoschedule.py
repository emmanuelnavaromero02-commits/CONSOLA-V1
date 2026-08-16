"""A1 — the SF foundation cycle must schedule itself on any install.

Diagnosis pinned by these tests: the whole automatic cycle already existed
(entity_scheduler -> sap_successfactors_extract_all -> Bronze -> Silver ->
Gold foundation + employees_anomalies -> Control Room); what was missing is a
scheduler-eligible entity_config row pointing at the cycle DAG. 99k/99l only
flip per-entity rows for a hardcoded FEMSA scope (a no-op everywhere else),
and those point at sap_successfactors_extract, which never reaches Gold.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "99zzz_sap_successfactors_cycle_autoschedule.sql"
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
    assert "99zzz_sap_successfactors_cycle_autoschedule.sql" in sql
    assert "INSERT INTO schema_migrations" in sql


def test_migration_resolves_scope_from_server_state_never_hardcoded():
    """99k/99l died because they hardcoded the FEMSA tenant/workspace UUIDs.
    The repair must resolve scope from installations / bootstrap names and
    no-op cleanly when neither exists."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(
        r"'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'", sql
    ), "no hardcoded tenant/workspace UUIDs"
    assert "cartridge_installations" in sql
    assert "'Default Tenant'" in sql and "'Main Workspace'" in sql
    assert "RETURN 0" in sql


def test_foundation_templates_bound_only_under_local_convention():
    """The live run showed the plan skipping every foundation entity with
    'scope_mismatch': templates carry a production connection id and no
    scope. The repair binds them to the local scope + 'default' connection,
    but ONLY when the resolved workspace is the dev bootstrap convention —
    a production install keeps its rows untouched."""
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
    """On a fresh install infra/init runs before any workspace exists, so the
    bootstrap (which creates the dev workspace + installations) must re-run
    the seeding function afterwards."""
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
    """Each cycle leaves a ledger timeline in pipeline_runs: per-entity
    'extracted' saves already existed; the Gold and anomaly-signal
    transitions must be recorded from the same helper."""
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
    """entity_scheduler conf carries scope but no pre-signed context; the
    admission task must self-sign from conf (same path extraction uses)
    instead of refusing every scheduler-fired run. Absent scope must still
    fail closed."""
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
    """Third live finding: the Console reveal nests credentials under
    'fields' while every consumer reads the payload flat; with an explicit
    conn_id there is no env fallback, so extraction failed CONFIG_INCOMPLETE
    despite a successful reveal. The client must lift 'fields' to the top
    level, with top-level identity keys winning on collision."""
    import sys

    sys.path.insert(
        0, str(REPO_ROOT / "cartridges" / "sap_successfactors")
    )
    try:
        from app.core.vault_client import _flatten_connection_fields
    finally:
        sys.path.pop(0)

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


def test_control_room_shows_freshness():
    presenter = PRESENTER.read_text(encoding="utf-8")
    assert "latestObservedAt" in presenter
    assert "formatRelativeFromNow" in presenter
    page = PAGE.read_text(encoding="utf-8")
    assert "Datos actualizados" in page
    assert "latestObservedAt(experience)" in page
