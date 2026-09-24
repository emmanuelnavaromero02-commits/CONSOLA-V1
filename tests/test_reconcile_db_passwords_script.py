from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/reconcile_db_passwords.sh"
MAKEFILE = REPO / "Makefile"


def test_reconcile_db_passwords_script_rotates_all_service_roles():
    src = SCRIPT.read_text(encoding="utf-8")
    for role in (
        "postgres",
        "omega_console",
        "omega_refinement",
        "omega_vault",
        "omega_workspace",
        "omega_mcp_infra",
        "omega_cartridge_sap_hcm",
        "omega_cartridge_sap_s4",
        "omega_cartridge_sap_sf",
        "omega_cartridge_sap_b1",
        "omega_airflow_dag",
        "omega_airflow_meta",
        "omega_superset_meta",
        "omega_cartridge_replicon",
        "omega_cartridge_salesforce",
        "omega_cartridge_hubspot",
        "omega_refinement_gold",
        "omega_gold_publisher",
    ):
        assert role in src
    assert "ALTER ROLE %I LOGIN PASSWORD %L" in src
    assert "current_setting('app.postgres_password', true)" in src
    assert "current_setting('app.omega_refinement_password', true)" in src
    assert "current_setting('app.omega_refinement_gold_password', true)" in src
    assert "current_setting('app.omega_gold_publisher_password', true)" in src


def test_reconcile_db_passwords_script_does_not_print_secret_values():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "set -x" not in src
    assert "echo ${OMEGA_" not in src
    assert "printf ${OMEGA_" not in src


def test_reconcile_db_passwords_make_target_exists():
    src = MAKEFILE.read_text(encoding="utf-8")
    assert "reconcile-db-passwords:" in src
    assert "scripts/reconcile_db_passwords.sh" in src
