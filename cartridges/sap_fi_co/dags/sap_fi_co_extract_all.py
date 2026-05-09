"""
sap_fi_co_extract_all DAG
=======================
Extrae TODAS las entidades llamando al cartucho SAP FI/CO Finanzas (Puerto 8207).
"""
from __future__ import annotations
import os
import httpx
from airflow.decorators import dag, task

CARTRIDGE_URL = "http://sap_fi_co:8207"

@dag(schedule=None, catchup=False)
def sap_fi_co_extract_all():
    @task
    def trigger_extract_all(**context):
        conf = context.get("dag_run").conf or {}

        headers = {
            "X-Api-Key": os.environ.get("INTERNAL_API_KEY", ""),
            "X-Internal-Service": "airflow",
        }

        with httpx.Client(timeout=300) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/skills/entities/extract-all",
                json=conf,
                headers=headers
            )
            res.raise_for_status()
            return res.json()

    trigger_extract_all()

sap_fi_co_extract_all()
