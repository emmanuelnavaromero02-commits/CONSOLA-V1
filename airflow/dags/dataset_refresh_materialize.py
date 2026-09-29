from __future__ import annotations

import functools
import hashlib
import time
from typing import Any, Callable

import httpx

from dataset_refresh_graph import _required_scope, _validate_plan
from dataset_refresh_idempotency import (
    MATERIALIZATION_LAYERS,
    finish_materialization,
    heartbeat_materialization,
    reserve_materialization,
)
from dataset_refresh_outcome import (
    RESULT_CLASSES,
    RESULT_DEGRADED,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_SKIPPED,
    classify_materialization_payload,
    missing_source_error_code,
)
from runtime_security_context import build_materialize_context
from service_job_client import ServiceJobError, run_service_job

REQUEST_TIMEOUT_SECONDS = 60
JOB_DEADLINE_SECONDS = 3 * 3600
HEARTBEAT_SECONDS = 60


def _safe_result(
    name: str,
    payload: dict[str, Any],
    *,
    reused: bool = False,
    classification: str = RESULT_OK,
) -> dict[str, Any]:
    return {
        "name": name,
        "layer": str(payload.get("layer") or ""),
        "ok": True,
        "classification": classification,
        "reused": reused,
        "row_count": int(payload.get("row_count") or 0),
    }


def _skipped_result(name: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": False,
        "classification": RESULT_SKIPPED,
        "reason": reason,
    }


def _plan_names(item: dict[str, Any], key: str) -> list[str]:
    value = item.get(key) or []
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry for entry in value
    ):
        raise RuntimeError("dataset refresh plan is malformed")
    return value


def _structural_absence(
    item: dict[str, Any],
    upstreams: list[str],
    never_materialized: list[str],
    refreshed: set[str],
    structurally_skipped: set[str],
    exc: Exception,
) -> str | None:
    if missing_source_error_code(exc) is None:
        return None
    absent = [upstream for upstream in upstreams if upstream in structurally_skipped]
    for source in never_materialized:
        if source in refreshed or source in absent:
            continue
        if source.startswith("raw/") and item.get("materialized") is not False:
            continue
        absent.append(source)
    return f"upstream_never_materialized:{absent[0]}" if absent else None


def _reused_layer(item: dict[str, Any], payload: dict[str, Any]) -> str:
    planned = str(item.get("layer") or "").strip()
    durable = str((payload or {}).get("layer") or "").strip()
    if planned and durable and planned != durable:
        raise RuntimeError("reused materialization layer contradicts the plan")
    layer = durable or planned
    if layer not in MATERIALIZATION_LAYERS:
        raise RuntimeError("reused materialization layer is unavailable")
    return layer


def _lease_keeper(postgres_dsn: str, **lease: Any) -> Callable[[], None]:
    last = [time.monotonic()]

    def keep() -> None:
        if time.monotonic() - last[0] >= HEARTBEAT_SECONDS:
            heartbeat_materialization(postgres_dsn, **lease)
            last[0] = time.monotonic()

    return keep


def _invoke_materialize(
    refinement_url: str,
    *,
    headers: dict[str, str],
    name: str,
    first_context: dict[str, Any],
    context_factory: Callable[[], dict[str, Any]],
    keep_lease: Callable[[], None],
    key: str,
) -> Any:
    contexts = [first_context]

    def body() -> dict[str, Any]:
        return {
            "tool": "materialize",
            "args": {"name": name},
            "security_context": contexts.pop() if contexts else context_factory(),
        }

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            return run_service_job(
                client,
                f"{refinement_url.rstrip('/')}/mcp/invoke",
                headers=headers,
                key=key,
                json=body,
                on_poll=keep_lease,
                deadline_seconds=JOB_DEADLINE_SECONDS,
            )
    except ServiceJobError as exc:
        if exc.status_code in {401, 403}:
            raise PermissionError("refinement rejected runtime authority") from exc
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            raise PermissionError("refinement rejected runtime authority") from exc
        raise RuntimeError("operational dependency unavailable") from exc


def materialize_in_order(
    context: dict[str, Any],
    *,
    postgres_dsn: str,
    refinement_url: str,
    headers: Callable[[str, dict[str, Any] | None], dict[str, str]],
    admitted_conf: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = context["ti"].xcom_pull(task_ids="resolve_chain", key="plan") or []
    cartridge = context["ti"].xcom_pull(task_ids="resolve_chain", key="cartridge_id")
    conf = admitted_conf or {}
    tenant_id, workspace_id = _required_scope(conf)
    cartridge_id = str(cartridge or conf.get("cartridge_id") or "").strip()
    if not cartridge_id:
        raise RuntimeError("scoped cartridge is unavailable")
    _validate_plan(plan, cartridge_id)
    if not plan:
        result = {
            "materialized": 0,
            "results": [],
            "status": "no_downstream_datasets",
            "breakdown": dict.fromkeys(RESULT_CLASSES, 0),
        }
        context["ti"].xcom_push(key="result", value=result)
        return result
    allow_partial = bool(conf.get("allow_partial"))
    results: list[dict[str, Any]] = []
    refreshed: set[str] = set()
    failure_chain: set[str] = set()
    structurally_skipped: set[str] = set()
    for item in plan:
        name = str(item["name"])
        upstreams = _plan_names(item, "upstreams")
        never_materialized = _plan_names(item, "never_materialized_upstreams")
        reservation = reserve_materialization(
            postgres_dsn,
            airflow_run_id=str(context["run_id"]),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            cartridge_id=cartridge_id,
            dataset=name,
        )
        if reservation.get("completed"):
            durable = reservation.get("result") or {}
            results.append(
                _safe_result(
                    name,
                    {**durable, "layer": _reused_layer(item, durable)},
                    reused=True,
                    classification=(
                        RESULT_DEGRADED
                        if durable.get("degraded") is True
                        else RESULT_OK
                    ),
                )
            )
            refreshed.add(name)
            continue
        slot_id = str(reservation["slot_id"])
        lease_token = int(reservation["lease_token"])
        finish = functools.partial(
            finish_materialization,
            postgres_dsn,
            slot_id=slot_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            lease_token=lease_token,
        )
        blocking = next((up for up in upstreams if up in failure_chain), None)
        if blocking is not None:
            reason = f"upstream_not_refreshed:{blocking}"
            finish(success=False, skipped_reason=reason)
            results.append(_skipped_result(name, reason))
            failure_chain.add(name)
            continue
        try:
            security_context = build_materialize_context(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                cartridge_id=cartridge_id,
                dataset_name=name,
                run_id=str(context["run_id"]),
            )
        except (RuntimeError, ValueError) as exc:
            finish(success=False)
            raise RuntimeError(
                "runtime materialization authority failed closed"
            ) from exc
        try:
            raw_payload = _invoke_materialize(
                refinement_url,
                headers=headers("REFINEMENT", context),
                name=name,
                first_context=security_context,
                context_factory=lambda: build_materialize_context(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    cartridge_id=cartridge_id,
                    dataset_name=name,
                    run_id=str(context["run_id"]),
                ),
                keep_lease=_lease_keeper(
                    postgres_dsn,
                    slot_id=slot_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    lease_token=lease_token,
                ),
                key="materialize:"
                + hashlib.sha256(f"{slot_id}:{lease_token}".encode()).hexdigest(),
            )
            classification, payload = classify_materialization_payload(
                raw_payload, expected_name=name
            )
            safe = _safe_result(name, payload, classification=classification)
            finish(
                success=True,
                result=safe,
                degraded=classification == RESULT_DEGRADED,
            )
            results.append(safe)
            refreshed.add(name)
        except Exception as exc:
            if isinstance(exc, (PermissionError, ValueError)):
                finish(success=False)
                raise RuntimeError(
                    "runtime materialization authority failed closed"
                ) from exc
            reason = _structural_absence(
                item,
                upstreams,
                never_materialized,
                refreshed,
                structurally_skipped,
                exc,
            )
            if reason is not None:
                finish(success=False, skipped_reason=reason)
                results.append(_skipped_result(name, reason))
                structurally_skipped.add(name)
            else:
                finish(success=False)
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "classification": RESULT_FAILED,
                        "error_code": type(exc).__name__,
                    }
                )
                failure_chain.add(name)
    breakdown = dict.fromkeys(RESULT_CLASSES, 0)
    for entry in results:
        breakdown[entry["classification"]] += 1
    failed = breakdown[RESULT_FAILED]
    result = {
        "materialized": breakdown[RESULT_OK] + breakdown[RESULT_DEGRADED],
        "results": results,
        "status": "completed",
        "breakdown": breakdown,
    }
    context["ti"].xcom_push(key="result", value=result)
    if failed and not allow_partial:
        raise RuntimeError("dataset_refresh_chain has failed materializations")
    return result
