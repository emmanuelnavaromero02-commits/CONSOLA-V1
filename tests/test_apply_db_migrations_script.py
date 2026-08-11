"""Sprint v1.32 — existing DB volumes need an explicit migration path."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_migrate_target():
    makefile = (REPO_ROOT / "Makefile").read_text()

    phony_lines = [line for line in makefile.splitlines() if line.startswith(".PHONY:")]
    phony_text = "\n".join(phony_lines)
    for target in (
        "help",
        "up",
        "up-core",
        "down",
        "test",
        "smoke",
        "migrate",
        "rotate-keys",
        "verify-release",
    ):
        assert target in phony_text
    assert "migrate:" in makefile
    assert "scripts/apply_db_migrations.sh" in makefile


def test_apply_db_migrations_tracks_schema_migrations_and_pgoptions():
    script = (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()
    guard = (REPO_ROOT / "scripts/migration_guard.py").read_text()

    assert "schema_migrations" in script
    assert "/docker-entrypoint-initdb.d/{filename}" in guard
    assert "99zzt_analytic_app_dataset_grants.sql" in guard
    assert "99zzu_analytic_app_manifest_registry.sql" in guard
    assert "both databases are inspected" in script
    assert "checksum_manifest_sha256" in guard
    assert "checksum_attested_at" in guard
    assert "PSQL_GOLD" in script
    assert "docker compose" in script
    assert "PGOPTIONS=" in script
    assert "app.omega_vault_password" in script
    assert "app.omega_refinement_gold_password" in script
    pgoptions_pairs = {
        "OMEGA_CONSOLE_PASSWORD": "app.omega_console_password",
        "OMEGA_OUTCOME_BINDER_PASSWORD": "app.omega_outcome_binder_password",
        "OMEGA_REFINEMENT_PASSWORD": "app.omega_refinement_password",
        "OMEGA_VAULT_PASSWORD": "app.omega_vault_password",
        "OMEGA_WORKSPACE_PASSWORD": "app.omega_workspace_password",
        "OMEGA_MCP_INFRA_PASSWORD": "app.omega_mcp_infra_password",
        "OMEGA_REFINEMENT_GOLD_PASSWORD": "app.omega_refinement_gold_password",
        "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD": "app.omega_cartridge_sap_hcm_password",
        "OMEGA_CARTRIDGE_SAP_S4_PASSWORD": "app.omega_cartridge_sap_s4_password",
        "OMEGA_CARTRIDGE_SAP_SF_PASSWORD": "app.omega_cartridge_sap_sf_password",
        "OMEGA_AIRFLOW_DAG_PASSWORD": "app.omega_airflow_dag_password",
        "OMEGA_AIRFLOW_META_PASSWORD": "app.omega_airflow_meta_password",
        "OMEGA_SUPERSET_META_PASSWORD": "app.omega_superset_meta_password",
        "OMEGA_CARTRIDGE_REPLICON_PASSWORD": "app.omega_cartridge_replicon_password",
        "OMEGA_CARTRIDGE_SALESFORCE_PASSWORD": "app.omega_cartridge_salesforce_password",
        "OMEGA_CARTRIDGE_HUBSPOT_PASSWORD": "app.omega_cartridge_hubspot_password",
        "OMEGA_CARTRIDGE_BANXICO_PASSWORD": "app.omega_cartridge_banxico_password",
        "OMEGA_CARTRIDGE_INEGI_PASSWORD": "app.omega_cartridge_inegi_password",
        "OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD": "app.omega_cartridge_sec_edgar_password",
    }
    for env_name, guc_name in pgoptions_pairs.items():
        assert env_name in script
        assert guc_name in script


def test_gold_role_migration_exists_for_fresh_gold_volumes():
    sql = (REPO_ROOT / "infra/init_gold/34_postgres_gold_role.sql").read_text()

    assert "CREATE ROLE omega_refinement_gold" in sql
    assert "GRANT USAGE, CREATE ON SCHEMA public TO omega_refinement_gold" in sql
    assert "app.omega_refinement_gold_password" in sql


def test_migration_46_picked_up_by_runner_glob():
    """v1.43.3: the runner uses ``infra/init/[0-9][0-9]_*.sql`` and
    bash globs lexicographically. Migration 46 must be the strictly
    largest filename under that pattern after this hotfix lands, so
    fresh boots apply 45's CASCADE→RESTRICT swap BEFORE 46 fixes the
    jobs ownership that 45 doesn't touch. If anyone later adds a 47+
    that re-touches jobs, this test still passes — we only assert
    "46 follows 45".
    """
    init = REPO_ROOT / "infra/init"
    matched = sorted(p.name for p in init.glob("[0-9][0-9]_*.sql"))
    assert "45_cascade_to_restrict.sql" in matched
    assert "46_sap_jobs_permissions.sql" in matched
    assert matched.index("45_cascade_to_restrict.sql") < matched.index(
        "46_sap_jobs_permissions.sql"
    )
