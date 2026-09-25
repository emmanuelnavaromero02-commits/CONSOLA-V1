from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]

SAP_HTTP_DAGS = [
    (REPO / "cartridges/sap_hcm/dags/sap_hcm_extract.py",
     "SAP_HCM_URL", "http://sap-hcm:8202"),
    (REPO / "cartridges/sap_hcm/dags/sap_hcm_extract_all.py",
     "SAP_HCM_URL", "http://sap-hcm:8202"),
    (REPO / "cartridges/sap_s4hana/dags/sap_s4hana_extract.py",
     "SAP_S4HANA_URL", "http://sap-s4hana:8204"),
    (REPO / "cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py",
     "SAP_S4HANA_URL", "http://sap-s4hana:8204"),
]

SUCCESSFACTORS_DIRECT_DAGS = [
    REPO / "cartridges/sap_successfactors/dags/sap_successfactors_extract.py",
    REPO / "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py",
]


@pytest.mark.parametrize("path,env_var,default_url", SAP_HTTP_DAGS,
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_sap_dag_uses_runtime_airflow_pair_key(path, env_var, default_url):
    src = path.read_text(encoding="utf-8")
    assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" in src, (
        f"{path.name} must use the Airflow→cartridge pair key"
    )
    assert "_internal_key()" in src, (
        f"{path.name} must resolve the pair key at task runtime"
    )
    assert "raise RuntimeError" in src, (
        f"{path.name} must raise RuntimeError when scoped key is missing in prod"
    )
    assert "_INTERNAL_API_KEY =" not in src, (
        f"{path.name} must not require API keys at module import/parse time"
    )


@pytest.mark.parametrize("path,env_var,default_url", SAP_HTTP_DAGS,
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_sap_dag_url_from_env(path, env_var, default_url):
    src = path.read_text(encoding="utf-8")
    pattern = rf'CARTRIDGE_URL\s*=\s*os\.environ\.get\(\s*[\'\"]{env_var}[\'\"]'
    assert re.search(pattern, src), (
        f"{path.name} must read CARTRIDGE_URL from env var {env_var}"
    )
    assert f'CARTRIDGE_URL = "{default_url}"' not in src, (
        f"{path.name} still has the hardcoded CARTRIDGE_URL"
    )
    assert default_url in src, (
        f"{path.name} must keep {default_url!r} as the env-lookup default"
    )


@pytest.mark.parametrize("path", [*SUCCESSFACTORS_DIRECT_DAGS], ids=lambda p: p.name)
def test_successfactors_dags_call_odata_directly(path):
    src = path.read_text(encoding="utf-8")
    assert "SAP_SUCCESSFACTORS_URL" not in src
    assert "http://sap-successfactors:8203" not in src
    assert "CARTRIDGE_URL" not in src
    assert "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE" not in src
    assert "run_entity" in src
    assert "set_security_context" in src
    assert "entity_config.connection_id" in src


@pytest.mark.parametrize("path,env_var,default_url", SAP_HTTP_DAGS,
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_sap_dag_has_default_args_with_retries(path, env_var, default_url):
    src = path.read_text(encoding="utf-8")
    assert "default_args" in src
    assert re.search(r'"retries"\s*:\s*[1-9]', src), (
        f"{path.name} must set retries >= 1 in default_args"
    )
    assert '"retry_exponential_backoff": True' in src, (
        f"{path.name} must enable retry_exponential_backoff"
    )
    assert "retry_delay" in src
    assert "max_retry_delay" in src
    assert "default_args=default_args" in src


@pytest.mark.parametrize("path", [*SUCCESSFACTORS_DIRECT_DAGS], ids=lambda p: p.name)
def test_successfactors_direct_dag_has_default_args_with_retries(path):
    src = path.read_text(encoding="utf-8")
    assert "default_args" in src
    assert re.search(r'"retries"\s*:\s*[1-9]', src)
    assert '"retry_exponential_backoff": True' in src
    assert "retry_delay" in src
    assert "max_retry_delay" in src
    assert "default_args=default_args" in src


def test_replicon_dag_still_has_default_args():
    p = REPO / "cartridges/replicon/dags/replicon_extract_all.py"
    if not p.exists():
        candidates = list((REPO / "cartridges/replicon/dags").glob("*.py"))
        assert candidates, "Replicon has no DAG files at all"
        p = candidates[0]
    src = p.read_text(encoding="utf-8")
    assert "default_args" in src or "retries" in src, (
        f"{p.name} appears to lack any retry configuration"
    )


def test_compose_exposes_cartridge_url_env_vars():
    compose = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    for env in (
        "REPLICON_URL",
        "HUBSPOT_URL",
        "SALESFORCE_URL",
        "SAP_HCM_URL",
        "SAP_S4HANA_URL",
    ):
        assert compose.count(env) >= 2, (
            f"{env} must be set on both airflow and airflow-scheduler "
            f"in infra/docker-compose.yml"
        )
