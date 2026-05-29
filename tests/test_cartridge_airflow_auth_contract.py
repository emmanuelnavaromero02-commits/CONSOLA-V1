from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

CARTRIDGES = (
    "replicon",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
)

SAP_DAGS = (
    "cartridges/sap_hcm/dags/sap_hcm_extract.py",
    "cartridges/sap_hcm/dags/sap_hcm_extract_all.py",
    "cartridges/sap_s4hana/dags/sap_s4hana_extract.py",
    "cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py",
    "cartridges/sap_successfactors/dags/sap_successfactors_extract.py",
    "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py",
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
            assert 'service == "airflow"' in src, rel
            assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" in src, rel
            assert "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE" in src, rel


def test_sap_airflow_dags_use_runtime_pair_key_not_parse_time_legacy_key():
    for rel in SAP_DAGS:
        src = _read(rel)
        assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" in src, rel
        assert '_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")' not in src, rel
        assert 'os.environ.get("INTERNAL_API_KEY", "")' in src, rel
        assert "legacy fallback disabled in production" in src, rel
