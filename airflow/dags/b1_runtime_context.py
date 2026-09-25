from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

try:
    from runtime_security_context import sign_runtime_context, verify_runtime_signature
except ModuleNotFoundError:
    from airflow.dags.runtime_security_context import (
        sign_runtime_context,
        verify_runtime_signature,
    )


CARTRIDGE_ID = "sap_b1"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _allowed_buckets() -> list[str]:
    buckets: list[str] = []
    for candidate in ("lakehouse", os.environ.get("MINIO_BUCKET"), os.environ.get("S3_BUCKET_NAME")):
        value = str(candidate or "").strip()
        if value and value not in buckets:
            buckets.append(value)
    return buckets


def _scope(tenant_id: Any, workspace_id: Any) -> tuple[str, str]:
    tenant = str(tenant_id or "").strip().lower()
    workspace = str(workspace_id or "").strip().lower()
    if not _UUID_RE.fullmatch(tenant) or not _UUID_RE.fullmatch(workspace):
        raise ValueError("sap_b1 runs need tenant_id and workspace_id UUIDs")
    return tenant, workspace


def sap_b1_security_context(tenant_id: Any, workspace_id: Any, *, user_id: str) -> dict[str, Any]:
    tenant, workspace = _scope(tenant_id, workspace_id)
    scope = f"tenant_id={tenant}/workspace_id={workspace}/"
    return sign_runtime_context(
        {
            "trusted": True,
            "source": "airflow",
            "user_id": user_id,
            "role": "admin",
            "workspace_role": "service",
            "tenant_id": tenant,
            "workspace_id": workspace,
            "permissions": ["cartridges.execute", "vault.secrets.reveal", "pipelines.run"],
            "allowed_cartridges": [CARTRIDGE_ID],
            "allowed_buckets": _allowed_buckets(),
            "allowed_prefixes": [
                f"raw/{CARTRIDGE_ID}/",
                f"silver/{CARTRIDGE_ID}/{scope}",
                f"gold/{CARTRIDGE_ID}/{scope}",
            ],
        }
    )


def security_context_from_conf(conf: Mapping[str, Any], *, user_id: str) -> dict[str, Any]:
    supplied = conf.get("security_context")
    tenant_id = conf.get("tenant_id")
    workspace_id = conf.get("workspace_id")
    if supplied is not None:
        if not isinstance(supplied, Mapping) or supplied.get("trusted") is not True:
            raise ValueError("trusted security_context is required when supplied")
        verify_runtime_signature(supplied)
        allowed = {str(item) for item in supplied.get("allowed_cartridges") or []}
        if "*" not in allowed and CARTRIDGE_ID not in allowed:
            raise ValueError("sap_b1 is not allowed by the supplied security_context")
        tenant_id = tenant_id or supplied.get("tenant_id")
        workspace_id = workspace_id or supplied.get("workspace_id")
        if _scope(tenant_id, workspace_id) != _scope(supplied.get("tenant_id"), supplied.get("workspace_id")):
            raise ValueError("security_context scope does not match the run")
    return sap_b1_security_context(tenant_id, workspace_id, user_id=user_id)


__all__ = ["CARTRIDGE_ID", "sap_b1_security_context", "security_context_from_conf"]
