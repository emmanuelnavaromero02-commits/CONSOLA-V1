"""
salesforce_extract_all DAG
==================================
Extrae TODAS las entidades llamando al cartucho Salesforce.

v1.43.1 (B1 + B2 + B3): see salesforce_extract.py.
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
    "SALESFORCE_URL", "http://salesforce:8205",
)

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
    "email_on_failure": False,
    "email_on_retry": False,
}


@dag(dag_id="salesforce_extract_all", schedule=None, catchup=False, default_args=default_args)
def salesforce_extract_all():
    @task
    def trigger_extract_all(**context):
        conf = context.get("dag_run").conf or {}

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }

        with httpx.Client(timeout=300) as client:
            skill_body = {
                key: conf[key]
                for key in ("tenant_id", "workspace_id", "security_context")
                if conf.get(key)
            }
            res = client.post(
                f"{CARTRIDGE_URL}/extract-all",
                params={"mode": conf.get("mode") or "incremental"},
                json=skill_body,
                headers=headers,
            )
            res.raise_for_status()
            return res.json()

    trigger_extract_all()


salesforce_extract_all()
