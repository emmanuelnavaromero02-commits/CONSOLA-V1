"""
sap_hcm_extract_all DAG
=======================
Extrae TODAS las entidades llamando al cartucho SAP HCM (Puerto 8202).
"""
from __future__ import annotations
import httpx
from airflow.decorators import dag, task

CARTRIDGE_URL = "http://sap_hcm:8202"

@dag(schedule=None, catchup=False)
def sap_hcm_extract_all():
    @task
    def trigger_extract_all(**context):
        conf = context.get("dag_run").conf or {}

        with httpx.Client(timeout=300) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/skills/entities/extract-all",
                json=conf,
                headers={"X-Internal-Service": "airflow"}
            )
            res.raise_for_status()
            return res.json()

    trigger_extract_all()

sap_hcm_extract_all()
