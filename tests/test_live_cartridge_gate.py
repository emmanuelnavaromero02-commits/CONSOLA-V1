from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_live_cartridge_script_is_gated_and_covers_all_priority_cartridges():
    script = _read("scripts/run_live_cartridge_checks.sh")
    for needle in (
        "OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS",
        "OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED",
        "TEST_PASSWORD or E2E_ADMIN_PASSWORD is required",
        "/api/cartridges/${cartridge}/test_connection",
        "/api/pipeline/${cartridge}/${entity}/extract",
        "OMEGA_LIVE_CARTRIDGE_RUN_EXTRACTION",
    ):
        assert needle in script
    for cartridge in (
        "hubspot",
        "salesforce",
        "replicon",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
    ):
        assert cartridge in script


def test_makefile_exposes_live_cartridge_gate():
    makefile = _read("Makefile")
    assert "live-cartridge-tests:" in makefile
    assert "bash scripts/run_live_cartridge_checks.sh" in makefile


def test_live_cartridge_runbook_documents_credentials_and_evidence():
    runbook = _read("docs/runbook/13_live_cartridge_validation.md")
    for needle in (
        "OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS=1",
        "OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED=1",
        "HubSpot private app token",
        "Salesforce sandbox Connected App",
        "SAP HCM OData sandbox credentials",
        "docs/release-evidence/",
    ):
        assert needle in runbook


def test_public_v1_script_checks_salesforce_live_connection():
    script = _read("scripts/verify_v1_public.sh")
    loop = script.split("verify_live_cartridges()", 1)[1].split("require_https_url", 1)[0]
    assert "salesforce" in loop
