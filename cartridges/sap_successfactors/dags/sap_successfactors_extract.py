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
from datetime import datetime, timezone, timedelta

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


def _mcp_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA", "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA missing; legacy fallback disabled in production")


def _mcp_headers() -> dict[str, str]:
    return {
        "X-Api-Key": _mcp_key(),
        "X-Internal-Service": "airflow",
    }

CARTRIDGE_URL = os.environ.get(
    "SAP_SUCCESSFACTORS_URL", "http://sap-successfactors:8203",
)
MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
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


def _first_int(payload: dict, *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    nested = payload.get("result")
    if isinstance(nested, dict):
        return _first_int(nested, *keys)
    return None


def _first_str(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = payload.get("result")
    if isinstance(nested, dict):
        return _first_str(nested, *keys)
    return None


def _pipeline_run_save(
    *,
    context: dict,
    conf: dict,
    entity: str,
    status: str,
    started_at: str,
    record_count: int | None = None,
    storage_uri: str | None = None,
    error_message: str | None = None,
) -> None:
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        print("[sap_successfactors_extract] pipeline_run_save skipped: missing tenant/workspace scope")
        return
    finished_at = datetime.now(timezone.utc).isoformat()
    airflow_run_id = context.get("run_id") or f"sap_successfactors_extract:{finished_at}"
    args = {
        "dag_id": "sap_successfactors_extract",
        "cartridge_id": "sap_successfactors",
        "entity": entity,
        "run_id": f"sap_successfactors_extract:{entity}:{airflow_run_id}",
        "airflow_dag_run_id": airflow_run_id,
        "mode": conf.get("mode") or "incremental",
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "extra": {
            "conn_id": conf.get("conn_id") or conf.get("connection_id") or None,
            "job_id": conf.get("job_id") or None,
        },
    }
    if record_count is not None:
        args["record_count"] = int(record_count)
    if storage_uri:
        args["storage_uri"] = storage_uri
    if error_message:
        args["error_message"] = error_message[:1000]
    payload = {"tool": "pipeline_run_save", "args": args}
    security_context = _security_context_from_conf(conf)
    if security_context:
        payload["security_context"] = security_context
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{MCP_INFRA_URL}/mcp/invoke",
                headers=_mcp_headers(),
                json=payload,
            )
            print(f"[sap_successfactors_extract] pipeline_run_save -> {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"[sap_successfactors_extract] pipeline_run_save failed: {exc}")


@dag(schedule=None, catchup=False, default_args=default_args, max_active_runs=2)
def sap_successfactors_extract():
    @task
    def trigger_extract(**context):
        conf = context.get("dag_run").conf or {}
        entity = conf.get("entity")
        if not entity:
            raise ValueError("entity parameter is required")
        logical_date = context.get("logical_date")
        started_at = (
            logical_date.isoformat()
            if hasattr(logical_date, "isoformat")
            else datetime.now(timezone.utc).isoformat()
        )

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

        params = {
            k: v for k, v in {
                "mode": conf.get("mode") or "incremental",
                "from_date": conf.get("from_date") or None,
                "to_date": conf.get("to_date") or None,
                "job_id": conf.get("job_id") or None,
                "conn_id": conf.get("conn_id") or conf.get("connection_id") or None,
            }.items() if v
        }
        try:
            with httpx.Client(timeout=300) as client:
                res = client.post(
                    f"{CARTRIDGE_URL}/entities/{entity}/extract",
                    params=params,
                    json=skill_body,
                    headers=headers,
                )
                res.raise_for_status()
                payload = res.json()
            _pipeline_run_save(
                context=context,
                conf=conf,
                entity=entity,
                status="success",
                started_at=started_at,
                record_count=_first_int(payload, "record_count", "rows_written", "records_written", "rows", "count"),
                storage_uri=_first_str(payload, "storage_uri", "path", "uri"),
            )
            return payload
        except Exception as exc:
            _pipeline_run_save(
                context=context,
                conf=conf,
                entity=entity,
                status="failed",
                started_at=started_at,
                error_message=str(exc),
            )
            raise

    trigger_extract()


dag = sap_successfactors_extract()
