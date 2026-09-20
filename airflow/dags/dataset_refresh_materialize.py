"""Signed, idempotent execution for ``dataset_refresh_chain``."""

from __future__ import annotations

import uuid
from typing import Any, Callable

import requests

from dataset_refresh_graph import _required_scope, _validate_plan
from dataset_refresh_idempotency import (
    MATERIALIZATION_LAYERS,
    finish_materialization,
    reserve_materialization,
)
from dataset_refresh_outcome import require_successful_materialization_response
from runtime_security_context import build_materialize_context


def _safe_result(
    name: str, payload: dict[str, Any], *, reused: bool = False
) -> dict[str, Any]:
    result = {
        "name": name,
        "layer": str(payload.get("layer") or ""),
        "ok": True,
        "reused": reused,
        "row_count": int(payload.get("row_count") or 0),
    }
    publication_run_id = str(payload.get("publication_run_id") or "").strip()
    if publication_run_id:
        try:
            result["publication_run_id"] = str(uuid.UUID(publication_run_id))
        except ValueError as exc:
            raise RuntimeError("materialization publication identity is invalid") from exc
    return result


def _reused_layer(item: dict[str, Any], payload: dict[str, Any]) -> str:
    """Resolve a completed slot's layer from the validated plan and its evidence."""
    planned = str(item.get("layer") or "").strip()
    durable = str((payload or {}).get("layer") or "").strip()
    if planned and durable and planned != durable:
        raise RuntimeError("reused materialization layer contradicts the plan")
    layer = durable or planned
    if layer not in MATERIALIZATION_LAYERS:
        raise RuntimeError("reused materialization layer is unavailable")
    return layer


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
        result = {"materialized": 0, "results": [], "status": "no_downstream_datasets"}
        context["ti"].xcom_push(key="result", value=result)
        return result
    allow_partial = bool(conf.get("allow_partial"))
    results: list[dict[str, Any]] = []
    for item in plan:
        name = str(item["name"])
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
                )
            )
            continue
        slot_id = str(reservation["slot_id"])
        lease_token = int(reservation["lease_token"])
        try:
            security_context = build_materialize_context(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                cartridge_id=cartridge_id,
                dataset_name=name,
                run_id=str(context["run_id"]),
            )
        except (RuntimeError, ValueError) as exc:
            finish_materialization(
                postgres_dsn,
                slot_id=slot_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                success=False,
            )
            raise RuntimeError(
                "runtime materialization authority failed closed"
            ) from exc
        try:
            response = requests.post(
                f"{refinement_url.rstrip('/')}/mcp/invoke",
                headers=headers("REFINEMENT", context),
                json={
                    "tool": "materialize",
                    "args": {"name": name},
                    "security_context": security_context,
                },
                timeout=600,
            )
            if response.status_code in {401, 403}:
                raise PermissionError("refinement rejected runtime authority")
            payload = require_successful_materialization_response(
                response, expected_name=name
            )
            safe = _safe_result(name, payload)
            finish_materialization(
                postgres_dsn,
                slot_id=slot_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                success=True,
                result=safe,
            )
            results.append(safe)
        except Exception as exc:
            finish_materialization(
                postgres_dsn,
                slot_id=slot_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                lease_token=lease_token,
                success=False,
            )
            if isinstance(exc, (PermissionError, ValueError)):
                raise RuntimeError(
                    "runtime materialization authority failed closed"
                ) from exc
            results.append(
                {"name": name, "ok": False, "error_code": type(exc).__name__}
            )
    materialized = sum(1 for item in results if item["ok"])
    result = {"materialized": materialized, "results": results, "status": "completed"}
    context["ti"].xcom_push(key="result", value=result)
    if materialized != len(results) and not allow_partial:
        raise RuntimeError("dataset_refresh_chain has failed materializations")
    return result
