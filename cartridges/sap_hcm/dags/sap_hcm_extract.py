"""
sap_hcm_extract DAG
===================
Extrae UNA entidad llamando al cartucho SAP HCM (Puerto 8202).
"""
from __future__ import annotations
import httpx
from airflow.decorators import dag, task

CARTRIDGE_URL = "http://sap_hcm:8202"

@dag(schedule=None, catchup=False)
def sap_hcm_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")

        with httpx.Client(timeout=300) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/skills/entities/{entity}/extract",
                json=conf,
                headers={"X-Internal-Service": "airflow"}
            )
            res.raise_for_status()
            return res.json()

    trigger_extract()

sap_hcm_extract()
