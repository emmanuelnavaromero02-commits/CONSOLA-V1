"""
sap_s4hana_extract DAG
======================
Extrae UNA entidad llamando al cartucho SAP S/4HANA.

v1.43.1 (Claude B1 + B2 + B3):
  B1 — fail-fast at parse-time on missing INTERNAL_API_KEY.
  B2 — cartridge URL from SAP_S4HANA_URL env var.
  B3 — default_args with exponential-backoff retries.
"""
from __future__ import annotations
import os
from datetime import timedelta

import httpx
from airflow.decorators import dag, task


_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")
if not _INTERNAL_API_KEY:
    raise RuntimeError(
        "INTERNAL_API_KEY missing — sap_s4hana_extract DAG cannot "
        "authenticate to the cartridge. Set the env var in the "
        "Airflow worker / scheduler."
    )

CARTRIDGE_URL = os.environ.get("SAP_S4HANA_URL", "http://sap-s4hana:8204")

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


@dag(schedule=None, catchup=False, default_args=default_args)
def sap_s4hana_extract():
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
            res = client.post(
                f"{CARTRIDGE_URL}/entities/{entity}/extract",
                json=conf,
                headers=headers,
            )
            res.raise_for_status()
            return res.json()

    trigger_extract()


dag = sap_s4hana_extract()
