"""
sap_successfactors_extract DAG
==============================
Extrae UNA entidad llamando directo a SuccessFactors OData.

El DAG no llama al contenedor sap-successfactors. Carga la configuracion de la
entidad, exige una conexion Vault explicita, ejecuta el cliente OData local del
cartucho como libreria y publica Bronze/telemetria desde Airflow.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException


MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
_SIGNATURE_FIELD = "_signature"
_SIGNED_AT_FIELD = "_signed_at"
_SIGNATURE_VERSION_FIELD = "_signature_version"
_SIGNATURE_VERSION = "hmac-sha256-v1"
_MIN_SIGNING_KEY_LEN = 32
_RUNTIME: SimpleNamespace | None = None

default_args = {
    "owner": "omega",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=20),
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


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
        unsigned = {
            key: value
            for key, value in ctx.items()
            if key not in {_SIGNATURE_FIELD, _SIGNED_AT_FIELD, _SIGNATURE_VERSION_FIELD}
        }
        return _sign_security_context(unsigned)
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


def _cartridge_root() -> Path:
    candidates = [
        Path(os.environ.get("SAP_SUCCESSFACTORS_CARTRIDGE_ROOT", "")),
        Path("/registry/cartridges/sap_successfactors"),
        Path("/opt/modecissions/cartridges/sap_successfactors"),
        Path(__file__).resolve().parents[1],
    ]
    for root in candidates:
        if str(root) and (root / "app").is_dir():
            return root
    raise RuntimeError("SAP SuccessFactors cartridge runtime not found; mount /registry/cartridges/sap_successfactors")


def _clear_conflicting_app_modules(root: Path) -> None:
    app_root = (root / "app").resolve()
    for name, module in list(sys.modules.items()):
        if name != "app" and not name.startswith("app."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file:
            try:
                if Path(module_file).resolve().is_relative_to(app_root):
                    continue
            except OSError:
                pass
        del sys.modules[name]


def _load_runtime() -> SimpleNamespace:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    root = _cartridge_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    _clear_conflicting_app_modules(root)
    refinement_triggers = importlib.import_module("app.core.refinement_triggers")
    _RUNTIME = SimpleNamespace(
        get_entity_config=importlib.import_module("app.services.catalog_service").get_entity_config,
        run_entity=importlib.import_module("app.services.extraction_service").run_entity,
        trigger_silver_refresh=refinement_triggers.trigger_silver_refresh,
        set_security_context=importlib.import_module("app.core.request_context").set_security_context,
        reset_security_context=importlib.import_module("app.core.request_context").reset_security_context,
    )
    return _RUNTIME


def _required_conn_id(conf: dict, config: dict) -> str:
    value = str(
        conf.get("conn_id")
        or conf.get("connection_id")
        or config.get("conn_id")
        or config.get("connection_id")
        or ""
    ).strip()
    if not value:
        entity = config.get("entity") or conf.get("entity") or "unknown"
        raise RuntimeError(
            f"SAP SuccessFactors entity {entity} requires entity_config.connection_id "
            "or dag_run.conf conn_id/connection_id; no default connection fallback is allowed."
        )
    return value


def _run_async(coro):
    return asyncio.run(coro)


def _is_non_retryable_successfactors_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "requires entity_config.connection_id" in text or "no default connection fallback" in text:
        return True
    if not ("successfactors" in text or "/odata/v2/" in text or "oauth/token" in text):
        return False
    if any(marker in text for marker in ("http 401", "http 403", "401 client error", "403 client error")):
        return True
    return any(
        marker in text
        for marker in (
            "http 400",
            "http 404",
            "400 client error",
            "404 client error",
            "bad request",
            "notfoundexception",
            " is not found",
            "invalid property",
            "invalid query option",
        )
    )


def _try_silver_refresh(runtime: SimpleNamespace, entity: str, security_context: dict | None) -> dict[str, Any]:
    try:
        payload = _run_async(runtime.trigger_silver_refresh(entity, security_context))
        if isinstance(payload, dict):
            return payload if payload.get("status") else {"status": "success", **payload}
        return {"status": "success"}
    except Exception as exc:  # noqa: BLE001 - Bronze extraction must remain the source of DAG success.
        message = f"{type(exc).__name__}: {exc}"
        print(f"[sap_successfactors_extract] silver refresh failed for {entity}: {message}")
        return {"status": "failed", "error": message}


def _downstream_status(payload: dict[str, Any], key: str) -> str:
    item = payload.get(key)
    if isinstance(item, dict):
        return str(item.get("status") or "").strip().lower()
    return ""


def _pipeline_status_for_success_payload(payload: dict[str, Any]) -> str:
    silver_status = _downstream_status(payload, "silver_refresh")
    if silver_status and silver_status not in {"success", "ok"}:
        return "partial"
    return "success"


def _pipeline_extra_from_payload(
    payload: dict[str, Any],
    *,
    conn_id: str | None,
    job_id: str | None,
) -> dict[str, Any]:
    record_count = _first_int(payload, "record_count", "rows_written", "records_written", "rows", "count")
    extra: dict[str, Any] = {
        "conn_id": conn_id,
        "job_id": job_id,
        "result_status": payload.get("status"),
        "empty_result": record_count == 0,
    }
    for key in (
        "silver_refresh",
        "incremental_filter_strategy",
        "incremental_fallback_reason",
        "retried_as_full_snapshot",
    ):
        if key in payload:
            extra[key] = payload.get(key)
    return extra


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


def _scope_from_conf(conf: dict) -> tuple[str, str]:
    ctx = conf.get("security_context")
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if isinstance(ctx, dict):
        tenant_id = tenant_id or str(ctx.get("tenant_id") or "").strip()
        workspace_id = workspace_id or str(ctx.get("workspace_id") or "").strip()
    return tenant_id, workspace_id


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
    extra: dict[str, Any] | None = None,
) -> None:
    tenant_id, workspace_id = _scope_from_conf(conf)
    if not tenant_id or not workspace_id:
        print("[sap_successfactors_extract] pipeline_run_save skipped: missing tenant/workspace scope")
        return
    finished_at = datetime.now(timezone.utc).isoformat()
    airflow_run_id = context.get("run_id") or f"sap_successfactors_extract:{finished_at}"
    args = {
        "dag_id": "sap_successfactors_extract",
        "cartridge_id": "sap_successfactors",
        "entity": entity,
        "run_id": airflow_run_id,
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
            **(extra or {}),
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
        runtime = _load_runtime()
        config = runtime.get_entity_config(str(entity))
        if not config:
            raise ValueError(f"Entity not found: {entity}")

        try:
            conn_id = _required_conn_id(conf, config)
        except Exception as exc:
            _pipeline_run_save(
                context=context,
                conf=conf,
                entity=str(entity),
                status="failed",
                started_at=started_at,
                error_message=str(exc),
                extra={
                    "classification": {
                        "status": "auth-blocked",
                        "code": "CONFIG_INCOMPLETE",
                    },
                },
            )
            raise AirflowFailException(str(exc)) from exc
        conf = {**conf, "conn_id": conn_id}
        security_context = _security_context_from_conf(conf)
        run_config: dict[str, Any] = {
            **config,
            "mode": conf.get("mode") or config.get("mode") or "incremental",
            "conn_id": conn_id,
        }
        for key in ("idempotency_key", "parent_idempotency_key"):
            if conf.get(key):
                run_config[key] = conf[key]
        if security_context:
            run_config["security_context"] = security_context

        token = runtime.set_security_context(security_context)
        try:
            payload = runtime.run_entity(
                run_config,
                from_date=conf.get("from_date") or None,
                to_date=conf.get("to_date") or None,
            )
            silver_refresh = _try_silver_refresh(runtime, str(entity), security_context)
            payload = {**payload, "silver_refresh": silver_refresh}
            _pipeline_run_save(
                context=context,
                conf=conf,
                entity=str(entity),
                status=_pipeline_status_for_success_payload(payload),
                started_at=started_at,
                record_count=_first_int(payload, "record_count", "rows_written", "records_written", "rows", "count"),
                storage_uri=_first_str(payload, "storage_uri", "path", "uri"),
                extra=_pipeline_extra_from_payload(
                    payload,
                    conn_id=conn_id,
                    job_id=str(conf.get("job_id") or "").strip() or None,
                ),
            )
            return payload
        except Exception as exc:
            _pipeline_run_save(
                context=context,
                conf=conf,
                entity=str(entity),
                status="failed",
                started_at=started_at,
                error_message=str(exc),
            )
            if _is_non_retryable_successfactors_error(exc):
                raise AirflowFailException(str(exc)) from exc
            raise
        finally:
            runtime.reset_security_context(token)

    trigger_extract()


dag = sap_successfactors_extract()
