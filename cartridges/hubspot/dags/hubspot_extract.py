"""
hubspot_extract DAG  (Pattern A — thin trigger → cartridge microservice)
========================================================================
Extrae UNA entidad de HubSpot llamando al microservicio del cartucho
(`/skills/run_{mode}/{entity}`) y luego propaga silver/gold aguas abajo
con el meta-DAG `dataset_refresh_chain`.

A diferencia de Replicon (Pattern B, cliente embebido en el DAG), aquí el
DAG es delgado: la extracción real (paginación, watermark, parquet) vive en
el microservicio del cartucho y se puede reusar por REST/MCP. Esto evita la
duplicación de lógica de extracción que tiene replicon_extract.py.

conf del run:
  entity   — nombre de la entidad (requerido; e.g. deals, companies)
  mode     — "incremental" | "full"  (default: incremental)
"""
from __future__ import annotations

import os
from datetime import timedelta

import httpx
import requests
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
    dag_id="hubspot_extract",
    description="Extrae una entidad de HubSpot → MinIO Bronze (vía microservicio) y propaga silver/gold",
    schedule=None,
    catchup=False,
    default_args=default_args,
    tags=["hubspot", "bronze", "extract"],
    params={
        "entity": {"type": "string", "default": "deals",
                   "description": "Entidad a extraer (e.g. deals, companies, contacts)"},
        "mode":   {"type": "string", "default": "incremental",
                   "description": "incremental | full"},
    },
)
def hubspot_extract():

    @task
    def extract(params: dict = None, **context) -> dict:
        dag_run = context.get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        conf = {**(params or {}), **run_conf}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")
        mode = (conf.get("mode") or "incremental").strip().lower()
        endpoint = "run_full_load" if mode == "full" else "run_incremental"

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }
        skill_body = {
            key: conf[key]
            for key in ("tenant_id", "workspace_id", "security_context")
            if conf.get(key)
        }
        with httpx.Client(timeout=600) as client:
            res = client.post(
                f"{CARTRIDGE_URL}/skills/{endpoint}/{entity}",
                json=skill_body,
                headers=headers,
            )
            res.raise_for_status()
            result = res.json()
        if isinstance(result, dict):
            result.setdefault("entity", entity)
        return result if isinstance(result, dict) else {"entity": entity, "result": result}

    @task
    def trigger_refresh_chain(result: dict) -> dict:
        """Propaga silver/gold aguas abajo con el meta-DAG dataset_refresh_chain."""
        import logging
        from airflow.operators.python import get_current_context

        log = logging.getLogger("airflow.task")
        if isinstance(result, dict) and result.get("record_count", 1) == 0:
            log.info("refresh_chain omitido (sin datos nuevos)")
            return {"triggered": False}

        entity = (result or {}).get("entity", "deals")
        dag_run = get_current_context().get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        refresh_conf = {
            "seed_raw": f"raw/hubspot/{entity}",
            "cartridge_id": "hubspot",
            "triggered_by": "hubspot_extract",
        }
        # Pass through SaaS scope when present (dataset_refresh_chain requires it).
        for key in ("tenant_id", "workspace_id", "security_context"):
            if run_conf.get(key):
                refresh_conf[key] = run_conf[key]

        airflow_url = os.environ.get("AIRFLOW_URL", "http://airflow:8080").rstrip("/")
        user = os.environ.get("AIRFLOW_USER") or os.environ.get("AIRFLOW_ADMIN_USER") or "admin"
        password = os.environ.get("AIRFLOW_PASSWORD") or os.environ.get("AIRFLOW_ADMIN_PASSWORD") or "admin"
        try:
            response = requests.post(
                f"{airflow_url}/api/v1/dags/dataset_refresh_chain/dagRuns",
                auth=(user, password),
                json={"conf": refresh_conf},
                timeout=15,
            )
            response.raise_for_status()
            log.info("refresh_chain disparado: %s", response.status_code)
            return {"triggered": True, "status": response.status_code}
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh_chain trigger fallo: %s", exc)
            return {"triggered": False, "error": str(exc)}

    trigger_refresh_chain(extract())


dag = hubspot_extract()
