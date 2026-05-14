"""
sap_s4hana_extract DAG
===================
Extrae UNA entidad llamando al cartucho SAP S/4HANA (Puerto 8204).
"""
from __future__ import annotations
import os
import httpx
from airflow.decorators import dag, task

CARTRIDGE_URL = "http://sap-s4hana:8204"

@dag(schedule=None, catchup=False)
def sap_s4hana_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")

        headers = {
            "X-Api-Key": os.environ.get("INTERNAL_API_KEY", ""),
            "X-Internal-Service": "airflow",
        }

        with httpx.Client(timeout=300) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/entities/{entity}/extract",
                json=conf,
                headers=headers
            )
            res.raise_for_status()
            return res.json()

    trigger_extract()

dag = sap_s4hana_extract()
