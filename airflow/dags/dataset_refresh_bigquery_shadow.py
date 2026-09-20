"""Best-effort trigger from a successful Gold publication into shadow parity."""

from __future__ import annotations

import os
import uuid
from typing import Any


TARGET_DATASET = "sap_successfactors_talent_9box"


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def trigger_talent_9box_shadow(
    *,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str,
    pipeline_run_id: str,
    materialization_status: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _enabled(os.environ.get("BIGQUERY_TALENT_9BOX_SHADOW_ENABLED")):
        return {"status": "not_applicable", "reason": "disabled"}
    allowlist = {
        item.strip().lower()
        for item in str(
            os.environ.get("BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST") or ""
        ).split(",")
        if item.strip()
    }
    if workspace_id.lower() not in allowlist:
        return {"status": "not_applicable", "reason": "workspace_not_allowlisted"}
    tenant_allowlist = {
        item.strip().lower()
        for item in str(
            os.environ.get("BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST") or ""
        ).split(",")
        if item.strip()
    }
    if tenant_id.lower() not in tenant_allowlist:
        return {"status": "not_applicable", "reason": "tenant_not_allowlisted"}
    if cartridge_id != "sap_successfactors" or materialization_status != "success":
        return {"status": "not_applicable", "reason": "source_not_successful"}
    matches = [
        item
        for item in results
        if isinstance(item, dict)
        and item.get("name") == TARGET_DATASET
        and item.get("layer") == "gold"
        and item.get("ok") is True
    ]
    if len(matches) != 1:
        return {"status": "not_applicable", "reason": "target_not_published"}
    try:
        source_run_id = str(uuid.UUID(str(matches[0]["publication_run_id"])))
        tenant_id = str(uuid.UUID(tenant_id))
        workspace_id = str(uuid.UUID(workspace_id))
    except (KeyError, ValueError):
        return {"status": "not_applicable", "reason": "publication_binding_missing"}
    conf = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "cartridge_id": cartridge_id,
        "dataset": TARGET_DATASET,
        "pipeline_run_id": pipeline_run_id,
        "source_run_id": source_run_id,
    }
    dag_run_id = f"shadow__{source_run_id}"
    try:
        from airflow.api.common.trigger_dag import trigger_dag

        trigger_dag(
            dag_id="bigquery_talent_9box_shadow",
            run_id=dag_run_id,
            conf=conf,
            replace_microseconds=False,
        )
    except Exception as exc:
        if exc.__class__.__name__ in {"DagRunAlreadyExists", "DagRunAlreadyExist"}:
            return {
                "status": "triggered",
                "reused": True,
                "source_run_id": source_run_id,
            }
        # The shadow must never block the authoritative PostgreSQL publication.
        return {
            "status": "trigger_failed",
            "error_code": type(exc).__name__,
            "source_run_id": source_run_id,
        }
    return {"status": "triggered", "reused": False, "source_run_id": source_run_id}


__all__ = ("TARGET_DATASET", "trigger_talent_9box_shadow")
