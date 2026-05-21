"""
sap_successfactors_extract DAG
==============================
Extrae UNA entidad llamando al cartucho SAP SuccessFactors.

v1.43.1 (Claude B1 + B2 + B3):
  B1 — fail-fast at parse-time on missing INTERNAL_API_KEY.
  B2 — cartridge URL from SAP_SUCCESSFACTORS_URL env var.
  B3 — default_args with exponential-backoff retries.
"""
from __future__ import annotations
import os
from datetime import timedelta

import httpx
from airflow.decorators import dag, task


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _internal_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE", "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE missing; legacy fallback disabled in production")

CARTRIDGE_URL = os.environ.get(
    "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203",
)

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


@dag(schedule=None, catchup=False, default_args=default_args)
def sap_successfactors_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }

        with httpx.Client(timeout=300) as client:
            params = {
                k: v for k, v in {
                    "mode": conf.get("mode") or "incremental",
                    "from_date": conf.get("from_date") or None,
                    "to_date": conf.get("to_date") or None,
                    "job_id": conf.get("job_id") or None,
                }.items() if v
            }
            res = client.post(
                f"{CARTRIDGE_URL}/entities/{entity}/extract",
                params=params,
                json={},
                headers=headers,
            )
            res.raise_for_status()
            return res.json()

    trigger_extract()


dag = sap_successfactors_extract()
