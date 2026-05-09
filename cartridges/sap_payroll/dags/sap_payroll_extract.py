"""
sap_payroll_extract DAG
===================
Extrae UNA entidad llamando al cartucho SAP Payroll (Puerto 8204).
"""
from __future__ import annotations
import os
import httpx
from airflow.decorators import dag, task

CARTRIDGE_URL = "http://sap_payroll:8204"

@dag(schedule=None, catchup=False)
def sap_payroll_extract():
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
                f"{CARTRIDGE_URL}/skills/entities/{entity}/extract",
                json=conf,
                headers=headers
            )
            res.raise_for_status()
            return res.json()

    trigger_extract()

sap_payroll_extract()
