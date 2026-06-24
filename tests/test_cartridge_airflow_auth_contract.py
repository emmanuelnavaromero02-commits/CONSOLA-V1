from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

CARTRIDGES = (
    "replicon",
    "hubspot",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
)

SAP_DAGS = (
    "cartridges/hubspot/dags/hubspot_extract.py",
    "cartridges/hubspot/dags/hubspot_extract_all.py",
    "cartridges/sap_hcm/dags/sap_hcm_extract.py",
    "cartridges/sap_hcm/dags/sap_hcm_extract_all.py",
    "cartridges/sap_s4hana/dags/sap_s4hana_extract.py",
    "cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py",
)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_cartridges_accept_airflow_pair_key_separately_from_console_key():
    for cartridge in CARTRIDGES:
        for rel in (
            f"cartridges/{cartridge}/app/api/deps.py",
            f"cartridges/{cartridge}/app/security.py",
        ):
            src = _read(rel)
            assert 'service == "airflow"' in src or 'x_internal_service == "airflow"' in src, rel
            assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" in src, rel
            assert "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE" in src, rel


def test_sap_airflow_dags_use_runtime_pair_key_not_parse_time_legacy_key():
    for rel in SAP_DAGS:
        src = _read(rel)
        assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" in src, rel
        assert '_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")' not in src, rel
        assert 'os.environ.get("INTERNAL_API_KEY", "")' in src, rel
        assert "legacy fallback disabled in production" in src, rel


def test_successfactors_child_dag_records_pipeline_run_telemetry():
    src = _read("cartridges/sap_successfactors/dags/sap_successfactors_extract.py")

    for needle in (
        "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
        "pipeline_run_save",
        "missing tenant/workspace scope",
        "_pipeline_status_for_success_payload",
        "status=_pipeline_status_for_success_payload(payload)",
        "\"partial\"",
        "status=\"failed\"",
        "record_count",
        "storage_uri",
        "airflow_dag_run_id",
    ):
        assert needle in src


def test_successfactors_airflow_dags_are_direct_not_airflow_to_cartridge_http():
    for rel in (
        "cartridges/sap_successfactors/dags/sap_successfactors_extract.py",
        "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py",
    ):
        src = _read(rel)
        assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" not in src, rel
        assert "SAP_SUCCESSFACTORS_URL" not in src, rel
        assert "http://sap-successfactors:8203" not in src, rel
        assert "run_entity" in src, rel
