from __future__ import annotations
import re
import os
from datetime import timedelta

import httpx
from airflow.decorators import dag, task


_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")


def _safe_name(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_NAME.fullmatch(text):
        raise ValueError(f"invalid {label}")
    return text


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
REQUEST_TIMEOUT_SECONDS = 60
JOB_DEADLINE_SECONDS = 4 * 3600

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


@dag(schedule=None, catchup=False, default_args=default_args)
def sap_b1_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = _safe_name(conf.get("entity"), "entity")
        if not entity:
            raise ValueError("entity parameter is required")

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }
        from b1_runtime_context import admission_time, sap_b1_security_context, security_context_from_conf

        verified = security_context_from_conf(
            conf, user_id="airflow:sap_b1_extract", admitted_at=admission_time(context.get("dag_run"))
        )

        def skill_body() -> dict:
            return {
                "security_context": sap_b1_security_context(
                    verified["tenant_id"], verified["workspace_id"], user_id="airflow:sap_b1_extract"
                )
            }

        from service_job_client import idempotency_key, run_service_job

        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            params = {
                k: v for k, v in {
                    "mode": conf.get("mode") or "incremental",
                    "from_date": conf.get("from_date") or None,
                    "to_date": conf.get("to_date") or None,
                    "job_id": conf.get("job_id") or None,
                }.items() if v
            }
            return run_service_job(
                client,
                f"{CARTRIDGE_URL}/entities/{entity}/extract",
                params=params,
                json=skill_body,
                headers=headers,
                key=idempotency_key(context, entity),
                deadline_seconds=JOB_DEADLINE_SECONDS,
            )

    trigger_extract()


dag = sap_b1_extract()
