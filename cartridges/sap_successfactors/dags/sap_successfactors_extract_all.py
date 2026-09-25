"""
sap_successfactors_extract_all DAG
==================================
Extrae entidades SAP SuccessFactors llamando directo a SuccessFactors OData.

No llama al contenedor sap-successfactors. Cada entidad debe traer
connection_id desde entity_config o recibir conn_id en dag_run.conf.
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
import requests
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException

try:
    from runtime_security_context import build_pipeline_run_context
except ModuleNotFoundError:
    _runtime_dags = next(
        parent / "airflow/dags"
        for parent in Path(__file__).resolve().parents
        if (parent / "airflow/dags/runtime_security_context.py").is_file()
    )
    sys.path.insert(0, str(_runtime_dags))
    from runtime_security_context import build_pipeline_run_context


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
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _mcp_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA", "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError(
        "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA missing; legacy fallback disabled in production"
    )


def _mcp_headers() -> dict[str, str]:
    return {
        "X-Api-Key": _mcp_key(),
        "X-Internal-Service": "airflow",
    }


def _signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _MIN_SIGNING_KEY_LEN:
        raise RuntimeError(
            "SECURITY_CONTEXT_SIGNING_KEY is required to sign extraction scope"
        )
    for name, value in os.environ.items():
        if (
            (name == "INTERNAL_API_KEY" or name.startswith("INTERNAL_API_KEY_"))
            and isinstance(value, str)
            and value.strip()
            and hmac.compare_digest(key, value.strip())
        ):
            raise RuntimeError(
                f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {name}"
            )
    return key


def _canonical_context(ctx: dict) -> bytes:
    payload = {key: value for key, value in ctx.items() if key != _SIGNATURE_FIELD}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


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
    return _sign_security_context(
        {
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
        }
    )


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
    raise RuntimeError(
        "SAP SuccessFactors cartridge runtime not found; mount /registry/cartridges/sap_successfactors"
    )


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
    extraction_status = importlib.import_module("app.core.extraction_status")
    extraction_service = importlib.import_module("app.services.extraction_service")
    minio_client = importlib.import_module("app.core.minio_client")
    _RUNTIME = SimpleNamespace(
        get_extract_all_plan=importlib.import_module(
            "app.services.catalog_service"
        ).get_extract_all_plan,
        run_entity=extraction_service.run_entity_with_metadata_guard,
        is_metadata_skip_result=extraction_service.is_metadata_skip_result,
        trigger_silver_refresh=refinement_triggers.trigger_silver_refresh,
        trigger_successfactors_gold_refresh=refinement_triggers.trigger_successfactors_gold_refresh,
        classify_successful_extraction=extraction_status.classify_successful_extraction,
        classify_extraction_exception=extraction_status.classify_extraction_exception,
        public_failure_message=extraction_status.public_failure_message,
        summarize_extraction_results=extraction_status.summarize_extraction_results,
        hard_failure_code=extraction_status.hard_failure_code,
        require_storage_access=minio_client.require_storage_access,
        set_security_context=importlib.import_module(
            "app.core.request_context"
        ).set_security_context,
        reset_security_context=importlib.import_module(
            "app.core.request_context"
        ).reset_security_context,
    )
    return _RUNTIME


def _selected_conn_id(conf: dict) -> str | None:
    value = str(conf.get("conn_id") or conf.get("connection_id") or "").strip()
    return value or None


def _required_config_conn_id(conf: dict, config: dict) -> str:
    value = str(
        _selected_conn_id(conf)
        or config.get("conn_id")
        or config.get("connection_id")
        or ""
    ).strip()
    if not value:
        entity = config.get("entity") or "unknown"
        raise RuntimeError(
            f"SAP SuccessFactors entity {entity} requires entity_config.connection_id "
            "or dag_run.conf conn_id/connection_id; no default connection fallback is allowed."
        )
    return value


def _entity_idempotency_key(base_key: str | None, entity: object) -> str | None:
    base = str(base_key or "").strip()
    entity_name = str(entity or "").strip()
    if not (base and entity_name):
        return None
    candidate = f"{base}:{entity_name}"
    if len(candidate) <= 180:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:24]
    return f"{base[:120]}:{digest}"


def _run_async(coro):
    return asyncio.run(coro)


def _try_silver_refresh(
    runtime: SimpleNamespace, entity: str, security_context: dict | None
) -> dict[str, Any]:
    try:
        payload = _run_async(runtime.trigger_silver_refresh(entity, security_context))
        if isinstance(payload, dict):
            return (
                payload if payload.get("status") else {"status": "success", **payload}
            )
        return {"status": "success"}
    except Exception:  # noqa: BLE001 - one Silver refresh must not hide a successful Bronze extract.
        print(f"[sap_successfactors_extract_all] silver refresh failed for {entity}")
        return {"status": "failed", "failure_code": "silver_refresh_failed"}


def _downstream_status(payload: dict[str, Any], key: str) -> str:
    item = payload.get(key)
    if isinstance(item, dict):
        return str(item.get("status") or "").strip().lower()
    return ""


def _pipeline_status_for_success_payload(payload: dict[str, Any]) -> str:
    if (
        payload.get("metadata_status") in {"select_pruned", "repeated_page_truncated"}
        or payload.get("metadata_pruned_fields")
        or payload.get("pagination_status") == "repeated_page_truncated"
    ):
        return "partial"
    silver_status = _downstream_status(payload, "silver_refresh")
    if silver_status and silver_status not in {"success", "ok"}:
        return "partial"
    return "success"


def _pipeline_extra_from_result(
    result: dict[str, Any],
    *,
    classified: dict[str, Any],
    entity_idempotency_key: str | None,
) -> dict[str, Any]:
    record_count = _first_int(
        result,
        "record_count",
        "rows_written",
        "records_written",
        "rows",
        "count",
    )
    return {
        "result_status": classified.get("status"),
        "empty_result": record_count == 0,
        "silver_refresh": result.get("silver_refresh"),
        "entity_idempotency_key": entity_idempotency_key,
        "incremental_filter_strategy": result.get("incremental_filter_strategy"),
        "incremental_fallback_reason": result.get("incremental_fallback_reason"),
        "retried_as_full_snapshot": result.get("retried_as_full_snapshot"),
        "metadata_status": result.get("metadata_status"),
        "metadata_pruned_fields": result.get("metadata_pruned_fields") or [],
        "metadata_missing_watermark_field": result.get(
            "metadata_missing_watermark_field"
        ),
        "metadata_missing_date_field": result.get("metadata_missing_date_field"),
    }


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


def _is_nonfatal_successfactors_block(classified: dict[str, Any]) -> bool:
    return classified.get("code") in {
        "SUCCESSFACTORS_METADATA_BLOCKED",
        "SUCCESSFACTORS_PERMISSION",
    }


def _pipeline_status_for_plan_outcome(outcome: dict[str, Any]) -> str:
    status = str(outcome.get("status") or "").strip().lower()
    if status in {"blocked", "skipped_explicit", "partial", "failed"}:
        return status
    if status == "skipped":
        return "skipped_explicit"
    return "blocked"


def _result_from_plan_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    status = _pipeline_status_for_plan_outcome(outcome)
    result_status = "skipped_explicit" if status == "skipped_explicit" else status
    return {
        "entity": outcome.get("entity"),
        "status": result_status,
        "reason": outcome.get("reason") or "unknown_error",
        "code": outcome.get("code") or "PLAN_OUTCOME",
        "odata_entity": outcome.get("odata_entity"),
        "fields_missing": outcome.get("fields_missing") or [],
        "metadata_status": outcome.get("metadata_status"),
        **({"error": outcome.get("error")} if outcome.get("error") else {}),
        **(
            {"failure_code": outcome.get("failure_code")}
            if outcome.get("failure_code")
            else {}
        ),
    }


def _should_count_as_extracted(result: dict[str, Any]) -> bool:
    return str(result.get("status") or "") in {"extracted", "partial"}


def _status_bucket(items: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    return [item for item in items if str(item.get("status") or "") == status]


def _pipeline_run_save(
    *,
    context: dict,
    conf: dict,
    entity: str,
    status: str,
    started_at: str,
    run_id_override: str | None = None,
    record_count: int | None = None,
    storage_uri: str | None = None,
    error_message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    tenant_id, workspace_id = _scope_from_conf(conf)
    if not tenant_id or not workspace_id:
        print(
            "[sap_successfactors_extract_all] pipeline_run_save skipped: missing tenant/workspace scope"
        )
        return
    finished_at = datetime.now(timezone.utc).isoformat()
    airflow_run_id = (
        context.get("run_id") or f"sap_successfactors_extract_all:{finished_at}"
    )
    args = {
        "dag_id": "sap_successfactors_extract_all",
        "cartridge_id": "sap_successfactors",
        "entity": entity,
        "run_id": run_id_override or f"{airflow_run_id}:{entity}",
        "airflow_dag_run_id": airflow_run_id,
        "mode": conf.get("mode") or "incremental",
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "extra": {
            "target": conf.get("target") or "all",
            "conn_id": conf.get("conn_id") or conf.get("connection_id") or None,
            "parent_idempotency_key": conf.get("idempotency_key") or None,
            **(extra or {}),
        },
    }
    if record_count is not None:
        args["record_count"] = int(record_count)
    if storage_uri:
        args["storage_uri"] = storage_uri
    if error_message:
        args["error_message"] = error_message[:1000]
    payload = {
        "tool": "pipeline_run_save",
        "args": args,
        "security_context": build_pipeline_run_context(args),
    }
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{MCP_INFRA_URL}/mcp/invoke",
                headers=_mcp_headers(),
                json=payload,
            )
            print(
                f"[sap_successfactors_extract_all] pipeline_run_save -> {response.status_code}: {response.text[:200]}"
            )
            response.raise_for_status()
    except Exception:  # noqa: BLE001
        print("[sap_successfactors_extract_all] pipeline_run_save failed")


@dag(schedule=None, catchup=False, default_args=default_args, max_active_runs=1)
def sap_successfactors_extract_all():
    @task
    def authorize_refresh_chain(**context) -> dict:
        from dataset_refresh_admission import build_dataset_refresh_trigger

        dag_run = context.get("dag_run")
        conf = dag_run.conf if dag_run and isinstance(dag_run.conf, dict) else {}
        upstream = conf.get("security_context")
        if not isinstance(upstream, dict):
            # A scheduler-fired cycle (entity_scheduler) carries the
            # tenant/workspace scope but no pre-signed context. Mint the same
            # service authority the console trigger passes — signed with the
            # platform key inside this perimeter, carrying the pipelines.run
            # permission the admission builder demands — and let it re-verify
            # signature, scope and cartridge; the purpose-bound envelope it
            # mints is unchanged. No scope at all still fails closed below.
            _sched_tenant = str(conf.get("tenant_id") or "").strip()
            _sched_workspace = str(conf.get("workspace_id") or "").strip()
            if _sched_tenant and _sched_workspace:
                upstream = _sign_security_context(
                    {
                        "trusted": True,
                        "source": "airflow",
                        "role": "admin",
                        "workspace_role": "service",
                        "tenant_id": _sched_tenant,
                        "workspace_id": _sched_workspace,
                        "permissions": ["pipelines.run", "cartridges.execute"],
                        "allowed_cartridges": ["sap_successfactors"],
                    }
                )
        if not isinstance(upstream, dict):
            raise RuntimeError("refresh chain admission authority is required")
        refresh_conf = {
            "seed_dataset": "sap_successfactors_employee_360",
            "cartridge_id": "sap_successfactors",
            "triggered_by": "sap_successfactors_extract_all",
            "tenant_id": conf.get("tenant_id") or upstream.get("tenant_id"),
            "workspace_id": conf.get("workspace_id") or upstream.get("workspace_id"),
        }
        return build_dataset_refresh_trigger(
            upstream_context=upstream,
            conf=refresh_conf,
            source_dag_run_id=str(getattr(dag_run, "run_id", "") or ""),
            prefix="successfactors",
        )

    @task
    def trigger_extract_all(admission: dict, **context):
        if not isinstance(admission, dict):
            raise RuntimeError("refresh chain admission is unavailable")
        conf = context.get("dag_run").conf or {}
        logical_date = context.get("logical_date")
        started_at = (
            logical_date.isoformat()
            if hasattr(logical_date, "isoformat")
            else datetime.now(timezone.utc).isoformat()
        )
        runtime = _load_runtime()
        security_context = _security_context_from_conf(conf)
        selected_conn_id = _selected_conn_id(conf)
        target = str(conf.get("target") or "all").strip().lower()
        mode = str(conf.get("mode") or "incremental").strip().lower()
        if mode not in {"full", "incremental"}:
            raise ValueError("mode must be full or incremental")
        if target not in {"all", "foundation", "talent"}:
            raise ValueError("target must be all, foundation or talent")

        token = runtime.set_security_context(security_context)
        aggregate_saved = False
        try:
            # Fail before catalog metadata discovery: downloading $metadata is
            # pointless when Bronze cannot be authenticated or written.
            runtime.require_storage_access()
            entities, skipped = runtime.get_extract_all_plan(
                conn_id=selected_conn_id,
                security_context=security_context,
                target=target,
            )
            results: list[dict[str, Any]] = []
            total_records = 0
            downstream_partial = False
            base_idempotency_key = (
                str(conf.get("idempotency_key") or "").strip() or None
            )

            for outcome in skipped:
                outcome_entity = str(outcome.get("entity") or "").strip()
                result = _result_from_plan_outcome(outcome)
                results.append(result)
                if not outcome_entity or outcome_entity.startswith("__"):
                    continue
                _pipeline_run_save(
                    context=context,
                    conf=conf,
                    entity=outcome_entity,
                    status=_pipeline_status_for_plan_outcome(outcome),
                    started_at=started_at,
                    error_message=outcome.get("error") or outcome.get("reason"),
                    extra={
                        "result_status": result["status"],
                        "reason": result.get("reason"),
                        "metadata_status": outcome.get("metadata_status"),
                        "fields_missing": outcome.get("fields_missing") or [],
                        "fields_used": outcome.get("fields_used") or [],
                        "plan_outcome": outcome,
                    },
                )

            for config in entities:
                entity = str(config.get("entity") or "").strip()
                entity_idempotency_key = _entity_idempotency_key(
                    base_idempotency_key, entity
                )
                try:
                    conn_id = _required_config_conn_id(conf, config)
                    effective_mode = (
                        mode
                        if mode == "full" or config.get("watermark_field")
                        else "full"
                    )
                    run_config: dict[str, Any] = {
                        **config,
                        "mode": effective_mode,
                        "conn_id": conn_id,
                    }
                    if entity_idempotency_key:
                        run_config["idempotency_key"] = entity_idempotency_key
                        run_config["parent_idempotency_key"] = base_idempotency_key
                    if security_context:
                        run_config["security_context"] = security_context
                    result = runtime.run_entity(run_config)
                    if runtime.is_metadata_skip_result(result):
                        results.append(result)
                        _pipeline_run_save(
                            context=context,
                            conf=conf,
                            entity=entity,
                            status="partial",
                            started_at=started_at,
                            error_message=str(
                                result.get("reason") or "missing_metadata"
                            ),
                            extra=_pipeline_extra_from_result(
                                result,
                                classified=result,
                                entity_idempotency_key=entity_idempotency_key,
                            ),
                        )
                        downstream_partial = True
                        continue
                    silver_refresh = _try_silver_refresh(
                        runtime, entity, security_context
                    )
                    result = {**result, "silver_refresh": silver_refresh}
                    total_records += int(result.get("record_count") or 0)
                    classified = runtime.classify_successful_extraction(result)
                    results.append(classified)
                    entity_pipeline_status = _pipeline_status_for_success_payload(
                        result
                    )
                    downstream_partial = (
                        downstream_partial or entity_pipeline_status == "partial"
                    )
                    _pipeline_run_save(
                        context=context,
                        conf=conf,
                        entity=entity,
                        status=entity_pipeline_status,
                        started_at=started_at,
                        record_count=_first_int(
                            result,
                            "record_count",
                            "rows_written",
                            "records_written",
                            "rows",
                            "count",
                        ),
                        storage_uri=_first_str(result, "storage_uri", "path", "uri"),
                        extra=_pipeline_extra_from_result(
                            result,
                            classified=classified,
                            entity_idempotency_key=entity_idempotency_key,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    classified = runtime.classify_extraction_exception(entity, exc)
                    failure_message = runtime.public_failure_message(classified)
                    results.append(classified)
                    entity_pipeline_status = (
                        "partial"
                        if _is_nonfatal_successfactors_block(classified)
                        else "failed"
                    )
                    _pipeline_run_save(
                        context=context,
                        conf=conf,
                        entity=entity,
                        status=entity_pipeline_status,
                        started_at=started_at,
                        error_message=failure_message,
                        extra={
                            "classification": classified,
                            "entity_idempotency_key": entity_idempotency_key,
                        },
                    )

            summary = runtime.summarize_extraction_results(results)
            gold_refresh = None
            if any(
                isinstance(item, dict) and _should_count_as_extracted(item)
                for item in results
            ):
                gold_refresh = _run_async(
                    runtime.trigger_successfactors_gold_refresh(
                        target, security_context
                    )
                )
                gold_status = (
                    str((gold_refresh or {}).get("status") or "").strip().lower()
                )
                downstream_partial = downstream_partial or gold_status not in {
                    "success",
                    "ok",
                }
                # Cycle transitions land in pipeline_runs — the ledger every
                # DAG already writes — so the run leaves a queryable timeline:
                # per-entity 'extracted' rows above, then Gold, then the
                # anomaly signal the Control Room surfaces.
                gold_results = [
                    item
                    for item in (gold_refresh or {}).get("results") or []
                    if isinstance(item, dict)
                ]
                _pipeline_run_save(
                    context=context,
                    conf=conf,
                    entity="gold_foundation",
                    status=(
                        "success" if gold_status in {"success", "ok"} else gold_status or "failed"
                    ),
                    started_at=started_at,
                    record_count=sum(
                        int(item.get("row_count") or 0) for item in gold_results
                    ),
                    extra={
                        "transition": "gold_materialized",
                        "target": target,
                        "materialized": (gold_refresh or {}).get("materialized"),
                        "total": (gold_refresh or {}).get("total"),
                        "silver_status": (gold_refresh or {}).get("silver_status"),
                        "datasets": {
                            str(item.get("name") or ""): item.get("row_count")
                            for item in gold_results
                        },
                    },
                )
                anomalies_item = next(
                    (
                        item
                        for item in gold_results
                        if str(item.get("name") or "").endswith("employees_anomalies")
                    ),
                    None,
                )
                if anomalies_item is not None:
                    anomalies_ok = anomalies_item.get("status") == "ok"
                    _pipeline_run_save(
                        context=context,
                        conf=conf,
                        entity="employee_central_anomalies",
                        status="success" if anomalies_ok else "failed",
                        started_at=started_at,
                        record_count=(
                            int(anomalies_item.get("row_count") or 0)
                            if anomalies_ok
                            else None
                        ),
                        error_message=(
                            None
                            if anomalies_ok
                            else str(anomalies_item.get("error") or "")[:500] or None
                        ),
                        extra={
                            "transition": "anomalies_detected",
                            "surface": "control_room.employee_central",
                            "surface_ready": anomalies_ok,
                            "anomalies": anomalies_item.get("row_count"),
                        },
                    )
            hard_failure = runtime.hard_failure_code(
                summary, results, skipped, attempted=len(entities)
            )
            hard_failed = hard_failure is not None
            blocked_or_failed = any(
                summary[key]
                for key in (
                    "auth_blocked",
                    "permission_blocked",
                    "failed_open",
                    "blocked",
                    "skipped_explicit",
                    "partial",
                )
            )
            aggregate_status = (
                "success"
                if not blocked_or_failed and not downstream_partial
                else "partial"
                if not hard_failed
                else "failed"
            )
            status_text = (
                "success"
                if aggregate_status == "success"
                else "failed"
                if hard_failed
                else "completed_with_blocks"
            )
            attempted = [
                item
                for item in results
                if item.get("entity") and not str(item.get("entity")).startswith("__")
            ]
            payload = {
                "status": status_text,
                "target": target,
                "mode": mode,
                "selected": len(entities),
                "attempted": len(attempted),
                "triggered": [
                    item
                    for item in results
                    if str(item.get("status") or "")
                    in {"extracted", "empty-valid", "partial"}
                ],
                "blocked": _status_bucket(results, "blocked"),
                "partial": _status_bucket(results, "partial"),
                "failed": [
                    item
                    for item in results
                    if str(item.get("status") or "")
                    in {"failed", "failed-open", "auth-blocked", "permission-blocked"}
                ],
                "skipped_explicit": _status_bucket(results, "skipped_explicit"),
                "total_records": total_records,
                "summary": summary,
                "results": results,
                "skipped": skipped,
                "outcomes": skipped,
                "gold_refresh": gold_refresh,
                "hard_failure": hard_failure,
            }
            _pipeline_run_save(
                context=context,
                conf=conf,
                entity="__extract_all__",
                status=aggregate_status,
                started_at=started_at,
                run_id_override=context.get("run_id"),
                record_count=total_records,
                error_message=hard_failure,
                extra={
                    "summary": summary,
                    "result_status": status_text,
                    "hard_failure": hard_failure,
                    "selected": len(entities),
                    "attempted": len(attempted),
                    "skipped": skipped,
                    "outcomes": skipped,
                    "gold_refresh": gold_refresh,
                    "idempotency_key": base_idempotency_key,
                },
            )
            aggregate_saved = True
            if hard_failed:
                raise AirflowFailException(hard_failure)
            return payload
        except Exception as exc:
            if isinstance(exc, AirflowFailException):
                raise
            if not aggregate_saved:
                classified = runtime.classify_extraction_exception(
                    "__extract_all__", exc
                )
                failure_message = runtime.public_failure_message(classified)
                _pipeline_run_save(
                    context=context,
                    conf=conf,
                    entity="__extract_all__",
                    status="failed",
                    started_at=started_at,
                    run_id_override=context.get("run_id"),
                    error_message=failure_message,
                    extra={
                        "classification": classified,
                        "target": target,
                        "mode": mode,
                        "idempotency_key": str(
                            conf.get("idempotency_key") or ""
                        ).strip()
                        or None,
                    },
                )
            raise RuntimeError(
                runtime.public_failure_message(
                    runtime.classify_extraction_exception("__extract_all__", exc)
                )
            ) from None
        finally:
            runtime.reset_security_context(token)

    @task
    def trigger_refresh_chain(result: dict, admission: dict) -> dict:
        """Puente Airflow -> inteligencia (P3).

        Tras la extraccion+gold del cartucho, dispara el meta-DAG
        dataset_refresh_chain para que materialice la cascada de talento en
        orden y notifique /internal/intelligence/gold-refresh -- exactamente
        lo que hoy solo hacen HubSpot y Replicon y de lo que carecia SAP
        SuccessFactors (por eso el dato extraido nunca alcanzaba los motores
        de decision por el reloj).

        Se dispara por REST, sin importar el job_runner del cartucho dentro del
        DAG, para respetar la separacion de responsabilidades del contrato de DAGs.
        """
        import logging

        log = logging.getLogger("airflow.task")
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
        except Exception as exc:
            raise RuntimeError("refresh_chain trigger failed closed") from exc

    authority = authorize_refresh_chain()
    trigger_refresh_chain(trigger_extract_all(authority), authority)


dag = sap_successfactors_extract_all()
