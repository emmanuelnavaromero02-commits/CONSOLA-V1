from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_replicon_cartridge_restored_with_dags_inside_cartridge():
    assert (ROOT / "cartridges/replicon").is_dir()
    assert (ROOT / "cartridges/replicon/dags/replicon_extract.py").is_file()
    assert not (ROOT / "airflow/dags/replicon_extract.py").exists()
    assert not (ROOT / "airflow/dags/replicon_extract_all.py").exists()


def test_remaining_cartridge_dirs_are_deployed_or_profiled():
    compose = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())
    service_names = set(compose["services"])
    cartridge_dirs = {p.name for p in (ROOT / "cartridges").iterdir() if p.is_dir()}

    assert cartridge_dirs == {
        "banxico",
        "hubspot",
        "inegi",
        "replicon",
        "salesforce",
        "sec_edgar",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "sap_b1",
    }
    assert {
        "banxico",
        "inegi",
        "replicon",
        "hubspot",
        "salesforce",
        "sec-edgar",
        "sap-hcm",
        "sap-s4hana",
        "sap-successfactors",
        "sap-b1",
    }.issubset(service_names)
