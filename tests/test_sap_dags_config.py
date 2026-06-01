"""Sprint v1.43.1 — Claude B1 + B2 + B3: SAP DAG hardening.

Each of the 6 SAP DAG modules must:
  * Read a scoped Airflow→cartridge key at task runtime (B1).
  * Read its cartridge URL from an env var (B2).
  * Declare ``default_args`` with retries + exponential backoff (B3).

We test by parsing the source — Airflow + httpx aren't installed in
the test environment, so we don't import the DAG; we read the file
and assert the contract.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]

SAP_DAGS = [
    # (path, expected env var name for URL, default-port-hostname)
    (REPO / "cartridges/sap_hcm/dags/sap_hcm_extract.py",
     "SAP_HCM_URL", "http://sap-hcm:8202"),
    (REPO / "cartridges/sap_hcm/dags/sap_hcm_extract_all.py",
     "SAP_HCM_URL", "http://sap-hcm:8202"),
    (REPO / "cartridges/sap_s4hana/dags/sap_s4hana_extract.py",
     "SAP_S4HANA_URL", "http://sap-s4hana:8204"),
    (REPO / "cartridges/sap_s4hana/dags/sap_s4hana_extract_all.py",
     "SAP_S4HANA_URL", "http://sap-s4hana:8204"),
    (REPO / "cartridges/sap_successfactors/dags/sap_successfactors_extract.py",
     "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"),
    (REPO / "cartridges/sap_successfactors/dags/sap_successfactors_extract_all.py",
     "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203"),
]


@pytest.mark.parametrize("path,env_var,default_url", SAP_DAGS,
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_sap_dag_uses_runtime_airflow_pair_key(path, env_var, default_url):
    """B1: the scheduler must be able to import DAGs even before runtime
    secrets are present, but task execution must refuse missing scoped keys
    in production."""
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


@pytest.mark.parametrize("path,env_var,default_url", SAP_DAGS,
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_sap_dag_url_from_env(path, env_var, default_url):
    """B2: CARTRIDGE_URL must come from the documented env var, with the
    compose service hostname as default (so local dev still works)."""
    src = path.read_text(encoding="utf-8")
    pattern = rf'CARTRIDGE_URL\s*=\s*os\.environ\.get\(\s*[\'\"]{env_var}[\'\"]'
    assert re.search(pattern, src), (
        f"{path.name} must read CARTRIDGE_URL from env var {env_var}"
    )
    # The hardcoded URL is the BUG — must NOT appear as a bare top-level
    # assignment any longer.
    assert f'CARTRIDGE_URL = "{default_url}"' not in src, (
        f"{path.name} still has the hardcoded CARTRIDGE_URL"
    )
    # But the default in the env lookup must include the compose hostname.
    assert default_url in src, (
        f"{path.name} must keep {default_url!r} as the env-lookup default"
    )


@pytest.mark.parametrize("path,env_var,default_url", SAP_DAGS,
                         ids=lambda v: v.name if isinstance(v, Path) else v)
def test_sap_dag_has_default_args_with_retries(path, env_var, default_url):
    """B3: default_args must declare retries with exponential backoff."""
    src = path.read_text(encoding="utf-8")
    assert "default_args" in src
    # Retries + exponential backoff explicit.
    assert re.search(r'"retries"\s*:\s*[1-9]', src), (
        f"{path.name} must set retries >= 1 in default_args"
    )
    assert '"retry_exponential_backoff": True' in src, (
        f"{path.name} must enable retry_exponential_backoff"
    )
    assert "retry_delay" in src
    assert "max_retry_delay" in src
    # And the @dag decorator must consume them.
    assert "default_args=default_args" in src


def test_replicon_dag_still_has_default_args():
    """Regression: the Replicon DAG already had default_args before
    v1.43.1; nothing in this hotfix should have stripped them."""
    p = REPO / "cartridges/replicon/dags/replicon_extract_all.py"
    if not p.exists():
        # The repo previously named the file differently — be lenient.
        # If neither shape exists, fail with a useful message.
        candidates = list((REPO / "cartridges/replicon/dags").glob("*.py"))
        assert candidates, "Replicon has no DAG files at all"
        p = candidates[0]
    src = p.read_text(encoding="utf-8")
    # We don't enforce specifics on Replicon — just that it has SOME
    # default_args / retry posture so the audit's B3 finding doesn't
    # silently regress here too.
    assert "default_args" in src or "retries" in src, (
        f"{p.name} appears to lack any retry configuration"
    )


def test_compose_exposes_cartridge_url_env_vars():
    """Local-dev safety net: compose must thread SAP_*_URL into the
    Airflow worker so the DAGs see the same defaults they had before
    the env-var refactor."""
    compose = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    for env in (
        "REPLICON_URL",
        "HUBSPOT_URL",
        "SALESFORCE_URL",
        "SAP_HCM_URL",
        "SAP_S4HANA_URL",
        "SAP_SUCCESSFACTORS_URL",
    ):
        # Each variable appears at least twice (airflow + airflow-scheduler).
        assert compose.count(env) >= 2, (
            f"{env} must be set on both airflow and airflow-scheduler "
            f"in infra/docker-compose.yml"
        )
