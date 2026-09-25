"""
sap_b1_extract_all DAG
==========================
Extrae TODAS las entidades llamando al cartucho SAP Business One.

v1.43.1 (Claude B1 + B2 + B3): see sap_b1_extract.py for rationale.
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

CARTRIDGE_URL = os.environ.get("SAP_B1_URL", "http://sap-b1:8206")

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


@dag(schedule=None, catchup=False, default_args=default_args)
def sap_b1_extract_all():
    @task
    def trigger_extract_all(**context):
        conf = context.get("dag_run").conf or {}

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }
        skill_body = {
            key: conf[key]
            for key in ("tenant_id", "workspace_id", "security_context")
            if conf.get(key)
        }

        # A Business One initial load reads every company; the cartridge answers
        # when it is done, so the client waits well beyond the OData default.
        with httpx.Client(timeout=3600) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/extract-all",
                params={"mode": conf.get("mode") or "incremental"},
                json=skill_body,
                headers=headers,
            )
            res.raise_for_status()
            return res.json()

    trigger_extract_all()


dag = sap_b1_extract_all()
