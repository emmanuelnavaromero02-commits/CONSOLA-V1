"""
hubspot_extract_all DAG  (Pattern A — thin trigger → cartridge microservice)
============================================================================
Extrae TODAS las entidades de HubSpot llamando al microservicio del cartucho
(`/skills/run_incremental_all` | `/skills/run_full_load_all`).

El refresh de silver/gold se dispara por entidad vía el DAG hubspot_extract,
o manualmente con dataset_refresh_chain una vez completada la ingesta.
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
    raise RuntimeError(
        "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE missing; legacy fallback disabled in production"
    )


CARTRIDGE_URL = os.environ.get("HUBSPOT_URL", "http://hubspot:8210")

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


@dag(
    dag_id="hubspot_extract_all",
    description="Extrae TODAS las entidades de HubSpot vía el microservicio del cartucho",
    schedule=None,
    catchup=False,
    default_args=default_args,
    tags=["hubspot", "bronze", "extract"],
    params={
        "mode": {"type": "string", "default": "incremental",
                 "description": "incremental | full"},
    },
)
def hubspot_extract_all():

    @task
    def trigger_extract_all(params: dict = None, **context) -> dict:
        dag_run = context.get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        conf = {**(params or {}), **run_conf}
        mode = (conf.get("mode") or "incremental").strip().lower()
        endpoint = "run_full_load_all" if mode == "full" else "run_incremental_all"

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }
        with httpx.Client(timeout=1200) as client:
            res = client.post(f"{CARTRIDGE_URL}/skills/{endpoint}", json={}, headers=headers)
            res.raise_for_status()
            return res.json()

    trigger_extract_all()


dag = hubspot_extract_all()
