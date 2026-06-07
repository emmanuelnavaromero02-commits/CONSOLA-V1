"""
sap_successfactors_extract DAG
==============================
Extrae UNA entidad llamando al cartucho SAP SuccessFactors.

v1.43.1 (Claude B1 + B2 + B3):
  B1 — fail-fast at parse-time on missing INTERNAL_API_KEY.
  B2 — cartridge URL from SAP_SUCCESSFACTORS_URL env var.
  B3 — default_args with exponential-backoff retries.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
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
    "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203",
)
_SIGNATURE_FIELD = "_signature"
_SIGNED_AT_FIELD = "_signed_at"
_SIGNATURE_VERSION_FIELD = "_signature_version"
_SIGNATURE_VERSION = "hmac-sha256-v1"
_MIN_SIGNING_KEY_LEN = 32

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


def _signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _MIN_SIGNING_KEY_LEN:
        raise RuntimeError("SECURITY_CONTEXT_SIGNING_KEY is required to sign extraction scope")
    for name, value in os.environ.items():
        if (
            (name == "INTERNAL_API_KEY" or name.startswith("INTERNAL_API_KEY_"))
            and isinstance(value, str)
            and value.strip()
            and hmac.compare_digest(key, value.strip())
        ):
            raise RuntimeError(f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {name}")
    return key


def _canonical_context(ctx: dict) -> bytes:
    payload = {key: value for key, value in ctx.items() if key != _SIGNATURE_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sign_security_context(ctx: dict) -> dict:
    signed = dict(ctx)
    signed[_SIGNED_AT_FIELD] = int(time.time())
    signed[_SIGNATURE_VERSION_FIELD] = _SIGNATURE_VERSION
    signed[_SIGNATURE_FIELD] = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical_context(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def _security_context_from_conf(conf: dict) -> dict | None:
    ctx = conf.get("security_context")
    if isinstance(ctx, dict) and ctx.get("trusted"):
        return ctx
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if not (tenant_id and workspace_id):
        return None
    return _sign_security_context({
        "trusted": True,
        "source": "console",
        "role": "admin",
        "workspace_role": "service",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "permissions": ["cartridges.execute", "vault.secrets.reveal"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_buckets": ["lakehouse"],
        "allowed_prefixes": [
            f"raw/sap_successfactors/tenant_id={tenant_id}/workspace_id={workspace_id}/",
            f"silver/sap_successfactors/tenant_id={tenant_id}/workspace_id={workspace_id}/",
            f"gold/sap_successfactors/tenant_id={tenant_id}/workspace_id={workspace_id}/",
        ],
    })


@dag(schedule=None, catchup=False, default_args=default_args)
def sap_successfactors_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")

        headers = {
            "X-Api-Key": _internal_key(),
            "X-Internal-Service": "airflow",
        }
        skill_body = {
            key: conf[key]
            for key in ("tenant_id", "workspace_id")
            if conf.get(key)
        }
        security_context = _security_context_from_conf(conf)
        if security_context:
            skill_body["security_context"] = security_context

        with httpx.Client(timeout=300) as client:
            params = {
                k: v for k, v in {
                    "mode": conf.get("mode") or "incremental",
                    "from_date": conf.get("from_date") or None,
                    "to_date": conf.get("to_date") or None,
                    "job_id": conf.get("job_id") or None,
                    "conn_id": conf.get("conn_id") or conf.get("connection_id") or None,
                }.items() if v
            }
            res = client.post(
                f"{CARTRIDGE_URL}/entities/{entity}/extract",
                params=params,
                json=skill_body,
                headers=headers,
            )
            res.raise_for_status()
            return res.json()

    trigger_extract()


dag = sap_successfactors_extract()
