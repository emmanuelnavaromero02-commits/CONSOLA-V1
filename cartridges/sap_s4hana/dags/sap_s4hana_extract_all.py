"""
sap_s4hana_extract_all DAG
==========================
Extrae TODAS las entidades llamando al cartucho SAP S/4HANA.

v1.43.1 (Claude B1 + B2 + B3): see sap_s4hana_extract.py for rationale.
"""
from __future__ import annotations
import os
from datetime import timedelta

import httpx
from airflow.decorators import dag, task


_INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")
if not _INTERNAL_API_KEY:
    raise RuntimeError(
        "INTERNAL_API_KEY missing — sap_s4hana_extract_all DAG cannot "
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
def sap_s4hana_extract_all():
    @task
    def trigger_extract_all(**context):
        conf = context.get("dag_run").conf or {}

        headers = {
            "X-Api-Key": _INTERNAL_API_KEY,
            "X-Internal-Service": "airflow",
        }

        with httpx.Client(timeout=300) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/extract-all",
                params={"mode": conf.get("mode") or "incremental"},
                json={},
                headers=headers,
            )
            res.raise_for_status()
            return res.json()

    trigger_extract_all()


dag = sap_s4hana_extract_all()
