"""Sprint v1.32 — only deployed/self-contained cartridges should remain.

Sprint v1.40 reversed the v1.32 deletion of the Replicon cartridge:
the cartridge is restored from the original ZIP and ships its own
service in compose, with DAGs bind-mounted into Airflow alongside
the SAP cartridges. The two assertions in this file were rewritten
to reflect the new shape.
"""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_replicon_cartridge_restored_with_dags_inside_cartridge():
    """v1.40: Replicon cartridge restored. Its DAGs ship inside the
    cartridge (cartridges/replicon/dags/), not as zombie copies in
    airflow/dags/."""
    assert (ROOT / "cartridges/replicon").is_dir()
    assert (ROOT / "cartridges/replicon/dags/replicon_extract.py").is_file()
    assert (ROOT / "cartridges/replicon/dags/replicon_extract_all.py").is_file()
    # The pre-v1.40 zombie copies in airflow/dags/ must be gone.
    assert not (ROOT / "airflow/dags/replicon_extract.py").exists()
    assert not (ROOT / "airflow/dags/replicon_extract_all.py").exists()


def test_remaining_cartridge_dirs_are_deployed_or_profiled():
    compose = yaml.safe_load((ROOT / "infra/docker-compose.yml").read_text())
    service_names = set(compose["services"])
    cartridge_dirs = {p.name for p in (ROOT / "cartridges").iterdir() if p.is_dir()}

    assert cartridge_dirs == {"replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"}
    assert {"replicon", "sap-hcm", "sap-s4hana", "sap-successfactors"}.issubset(service_names)
