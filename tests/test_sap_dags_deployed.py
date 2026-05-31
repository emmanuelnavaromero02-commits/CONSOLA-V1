"""Sprint v1.30 — SAP cartridge DAGs must be visible to Airflow."""
from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
AWS_COMPOSE = REPO_ROOT / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml"
AIRFLOW_DAGS = REPO_ROOT / "airflow" / "dags"
CARTRIDGES = REPO_ROOT / "cartridges"

SAP_DAGS = {
    "sap_hcm": ("sap_hcm_extract.py", "sap_hcm_extract_all.py"),
    "sap_s4hana": ("sap_s4hana_extract.py", "sap_s4hana_extract_all.py"),
    "sap_successfactors": (
        "sap_successfactors_extract.py",
        "sap_successfactors_extract_all.py",
    ),
}


def _load_compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _service_volumes(compose_path: Path, service: str) -> list[str]:
    compose = _load_compose(compose_path)
    return compose["services"][service].get("volumes", [])


def _assert_local_sap_mount(cartridge: str) -> None:
    expected = f"../cartridges/{cartridge}/dags:/opt/airflow/dags/{cartridge}:ro"
    for service in ("airflow", "airflow-scheduler"):
        volumes = _service_volumes(LOCAL_COMPOSE, service)
        assert expected in volumes, (
            f"{service} must mount {cartridge} DAGs into Airflow; volumes={volumes!r}"
        )


def _assert_aws_sap_mount(cartridge: str) -> None:
    expected = (
        f"/opt/modecissions/cartridges/{cartridge}/dags:"
        f"/opt/airflow/dags/{cartridge}:ro"
    )
    for service in ("airflow", "airflow-scheduler"):
        volumes = _service_volumes(AWS_COMPOSE, service)
        assert expected in volumes, (
            f"{service} AWS compose must mount {cartridge} DAGs into Airflow; "
            f"volumes={volumes!r}"
        )


def _assert_sap_dag_files_exist(cartridge: str) -> None:
    dag_dir = CARTRIDGES / cartridge / "dags"
    assert dag_dir.is_dir(), f"missing cartridge DAG dir: {dag_dir}"
    for filename in SAP_DAGS[cartridge]:
        assert (dag_dir / filename).is_file(), f"missing SAP DAG source: {dag_dir / filename}"


def test_sap_hcm_dag_present_in_airflow_dags():
    _assert_sap_dag_files_exist("sap_hcm")
    _assert_local_sap_mount("sap_hcm")
    _assert_aws_sap_mount("sap_hcm")


def test_sap_s4hana_dag_present_in_airflow_dags():
    _assert_sap_dag_files_exist("sap_s4hana")
    _assert_local_sap_mount("sap_s4hana")
    _assert_aws_sap_mount("sap_s4hana")


def test_sap_successfactors_dag_present_in_airflow_dags():
    _assert_sap_dag_files_exist("sap_successfactors")
    _assert_local_sap_mount("sap_successfactors")
    _assert_aws_sap_mount("sap_successfactors")


def test_sap_dags_are_valid_python():
    for cartridge, filenames in SAP_DAGS.items():
        for filename in filenames:
            path = CARTRIDGES / cartridge / "dags" / filename
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_no_orphan_cartridge_dags():
    """Every cartridge DAG source must be readable by Airflow somehow.

    Sprint v1.40: every cartridge (SAP + Replicon) now keeps DAGs
    self-contained inside ``cartridges/<id>/dags/`` and exposes them
    through compose bind mounts. The pre-v1.40 fallback that tolerated
    runtime copies in ``airflow/dags/`` is gone — those zombie copies
    were deleted when the Replicon cartridge was restored.
    """
    cartridge_dags = sorted(CARTRIDGES.glob("*/dags/*.py"))
    assert cartridge_dags, "expected cartridge DAG sources"

    for path in cartridge_dags:
        cartridge = path.parents[1].name
        if cartridge in SAP_DAGS:
            _assert_local_sap_mount(cartridge)
            continue
        if cartridge in ("replicon", "hubspot", "salesforce"):
            # v1.40: replicon DAGs are bind-mounted just like SAP.
            # The HubSpot CRM cartridge follows the same self-contained pattern.
            expected = f"../cartridges/{cartridge}/dags:/opt/airflow/dags/{cartridge}:ro"
            for service in ("airflow", "airflow-scheduler"):
                volumes = _service_volumes(LOCAL_COMPOSE, service)
                assert expected in volumes, (
                    f"{service} must bind-mount {cartridge} DAGs; got {volumes!r}"
                )
            continue
        raise AssertionError(
            f"{path}: cartridge {cartridge!r} has DAGs but no compose mount "
            f"and no airflow/dags fallback"
        )


def test_no_root_sap_cartridge_dag_copies():
    """SAP cartridge DAG sources must not be copied into airflow/dags.

    Airflow reads the canonical cartridge DAGs through read-only compose
    mounts. Root-level runtime copies drift from the cartridge sources and
    caused stale zombie DAGs to survive after regeneration.
    """
    leftovers = sorted(
        path.relative_to(REPO_ROOT)
        for path in AIRFLOW_DAGS.glob("sap_*.py")
    )
    assert leftovers == [], f"unexpected root SAP DAG copies: {leftovers!r}"
