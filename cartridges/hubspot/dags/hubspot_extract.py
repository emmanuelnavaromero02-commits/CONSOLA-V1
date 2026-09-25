from __future__ import annotations

import re
import os
from datetime import timedelta

import httpx
import requests
from airflow.decorators import dag, task


_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")


def _safe_name(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_NAME.fullmatch(text):
        raise ValueError(f"invalid {label}")
    return text


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


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
REQUEST_TIMEOUT_SECONDS = 60
JOB_DEADLINE_SECONDS = 2 * 3600


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
        "entity": {
            "type": "string",
            "default": "deals",
            "description": "Entidad a extraer (e.g. deals, companies, contacts)",
        },
        "mode": {
            "type": "string",
            "default": "incremental",
            "description": "incremental | full",
        },
    },
)
def hubspot_extract():
    @task
    def authorize_refresh_chain(params: dict = None, **context) -> dict:
        from dataset_refresh_admission import build_dataset_refresh_trigger

        dag_run = context.get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        conf = {**(params or {}), **run_conf}
        entity = _safe_name(conf.get("entity"), "entity")
        upstream = conf.get("security_context")
        if not entity or not isinstance(upstream, dict):
            raise RuntimeError("refresh chain admission authority is required")
        refresh_conf = {
            "seed_raw": f"raw/hubspot/{entity}",
            "cartridge_id": "hubspot",
            "triggered_by": "hubspot_extract",
            "tenant_id": conf.get("tenant_id") or upstream.get("tenant_id"),
            "workspace_id": conf.get("workspace_id") or upstream.get("workspace_id"),
        }
        return build_dataset_refresh_trigger(
            upstream_context=upstream,
            conf=refresh_conf,
            source_dag_run_id=str(getattr(dag_run, "run_id", "") or ""),
            prefix="hubspot",
        )

    @task
    def extract(admission: dict, params: dict = None, **context) -> dict:
        if not isinstance(admission, dict):
            raise RuntimeError("refresh chain admission is unavailable")
        dag_run = context.get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        conf = {**(params or {}), **run_conf}
        entity = _safe_name(conf.get("entity"), "entity")
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
        from service_job_client import idempotency_key, run_service_job

        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            result = run_service_job(
                client,
                f"{CARTRIDGE_URL}/skills/{endpoint}/{entity}",
                json=skill_body,
                headers=headers,
                key=idempotency_key(context, entity, endpoint),
                deadline_seconds=JOB_DEADLINE_SECONDS,
            )
        if isinstance(result, dict):
            result.setdefault("entity", entity)
        return (
            result if isinstance(result, dict) else {"entity": entity, "result": result}
        )

    @task
    def trigger_refresh_chain(result: dict, admission: dict) -> dict:
        import logging

        log = logging.getLogger("airflow.task")
        if isinstance(result, dict) and result.get("record_count", 1) == 0:
            log.info("refresh_chain omitido (sin datos nuevos)")
            return {"triggered": False}

        if not isinstance(admission, dict) or not isinstance(
            admission.get("conf"), dict
        ):
            raise RuntimeError("refresh chain admission is unavailable")
        dag_run_id = str(admission.get("dag_run_id") or "")
        refresh_conf = dict(admission["conf"])

        airflow_url = os.environ.get("AIRFLOW_URL", "http://airflow:8080").rstrip("/")
        user = (
            os.environ.get("AIRFLOW_USER")
            or os.environ.get("AIRFLOW_ADMIN_USER")
            or "admin"
        )
        password = (
            os.environ.get("AIRFLOW_PASSWORD")
            or os.environ.get("AIRFLOW_ADMIN_PASSWORD")
            or "admin"
        )
        try:
            response = requests.post(
                f"{airflow_url}/api/v1/dags/dataset_refresh_chain/dagRuns",
                auth=(user, password),
                json={"conf": refresh_conf, "dag_run_id": dag_run_id},
                timeout=15,
            )
            response.raise_for_status()
            log.info("refresh_chain disparado: %s", response.status_code)
            return {"triggered": True, "status": response.status_code}
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("refresh_chain trigger failed closed") from exc

    authority = authorize_refresh_chain()
    trigger_refresh_chain(extract(authority), authority)


dag = hubspot_extract()
