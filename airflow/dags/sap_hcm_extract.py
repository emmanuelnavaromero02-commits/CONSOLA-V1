"""
sap_hcm_extract DAG
===================
Extrae UNA entidad llamando al cartucho SAP HCM.

v1.43.1 (Claude B1 + B2 + B3):
  B1 — fail-fast at parse-time if INTERNAL_API_KEY is missing instead of
       silently sending unauthenticated requests with an empty key.
  B2 — cartridge URL comes from SAP_HCM_URL env var (with the compose
       service hostname as a dev default), so the DAG is portable to
       Kubernetes / MWAA / Composer without code edits.
  B3 — default_args with exponential-backoff retries so transient
       failures (cartridge restart, network blip) don't fail the run on
       the first error.
"""
from __future__ import annotations
import os
from datetime import timedelta

import httpx
from airflow.decorators import dag, task


# B1 — fail-fast at parse-time. The DAG file is imported by the
# scheduler; if the key is missing we want the scheduler to surface
# the misconfiguration immediately, not at task-run time.
_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")
if not _INTERNAL_API_KEY:
    raise RuntimeError(
        "INTERNAL_API_KEY missing — sap_hcm_extract DAG cannot "
        "authenticate to the cartridge. Set the env var in the "
        "Airflow worker / scheduler."
    )

# B2 — env var, with the compose service hostname as the dev default.
CARTRIDGE_URL = os.environ.get("SAP_HCM_URL", "http://sap-hcm:8202")

# B3 — retry on transient failures.
default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


@dag(
    is_paused_upon_creation=False,schedule=None, catchup=False, default_args=default_args)
def sap_hcm_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")

        headers = {
            "X-Api-Key": _INTERNAL_API_KEY,
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


dag = sap_hcm_extract()
