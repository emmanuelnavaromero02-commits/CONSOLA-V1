"""Sprint v1.32 — only deployed/self-contained cartridges should remain."""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_replicon_microservice_cartridge_removed_but_runtime_dags_remain():
    assert not (ROOT / "cartridges/replicon").exists()
    assert (ROOT / "airflow/dags/replicon_extract.py").is_file()
    assert (ROOT / "airflow/dags/replicon_extract_all.py").is_file()


def test_remaining_cartridge_dirs_are_deployed_or_profiled():
    compose = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())
    service_names = set(compose["services"])
    cartridge_dirs = {p.name for p in (ROOT / "cartridges").iterdir() if p.is_dir()}

    assert cartridge_dirs == {"sap_hcm", "sap_s4hana", "sap_successfactors"}
    assert {"sap-hcm", "sap-s4hana", "sap-successfactors"}.issubset(service_names)
