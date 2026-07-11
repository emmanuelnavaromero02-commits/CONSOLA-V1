"""
sec_edgar_extract DAG
=====================
Manual Bronze-only extraction for SEC EDGAR company facts.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

import httpx
from airflow.decorators import dag, task

CARTRIDGE_URL = os.environ.get("SEC_EDGAR_URL", "http://sec-edgar:8217")
DEFAULT_CONN_ID = "default"


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
    raise RuntimeError("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE missing")


def _response_error(response: httpx.Response) -> str:
    text = response.text.replace("\n", " ").strip()
    if len(text) > 500:
        text = f"{text[:500]}..."
    return f"SEC EDGAR cartridge request failed status={response.status_code} body={text}"


default_args = {
    "owner": "omega",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
}


@dag(
    dag_id="sec_edgar_extract",
    description="Manual SEC EDGAR Bronze-only extraction",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["sec_edgar", "bronze", "extract"],
    params={
        "mode": {"type": "string", "default": "incremental"},
        "tenant_id": {"type": "string", "default": ""},
        "workspace_id": {"type": "string", "default": ""},
    },
)
def sec_edgar_extract():
    @task
    def extract(params: dict | None = None, **context: Any) -> dict:
        dag_run = context.get("dag_run")
        run_conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        conf = {**(params or {}), **run_conf}
        tenant_id = str(conf.get("tenant_id") or "")
        workspace_id = str(conf.get("workspace_id") or "")
        if not tenant_id or not workspace_id:
            raise ValueError("tenant_id and workspace_id are required")
        mode = str(conf.get("mode") or "incremental").strip().lower()
        endpoint = "run_full_load" if mode == "full" else "run_incremental"
        body = {
            key: conf[key]
            for key in ("tenant_id", "workspace_id", "from_date", "to_date", "ciks", "run_id")
            if conf.get(key) is not None
        }
        conn_id = str(conf.get("conn_id") or DEFAULT_CONN_ID).strip()
        headers = {"X-Api-Key": _internal_key(), "X-Internal-Service": "airflow"}
        security_context = conf.get("security_context") if isinstance(conf.get("security_context"), dict) else None
        if security_context:
            headers["X-Security-Context"] = json.dumps(security_context, ensure_ascii=False)
        with httpx.Client(timeout=900) as client:
            response = client.post(
                f"{CARTRIDGE_URL}/skills/{endpoint}/company_facts",
                json=body,
                headers=headers,
                params={"conn_id": conn_id} if conn_id else None,
            )
            if response.status_code >= 400:
                raise RuntimeError(_response_error(response))
            return response.json()

    extract()


dag = sec_edgar_extract()
