from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

try:
    from runtime_security_context import sign_runtime_context, verify_runtime_signature
except ModuleNotFoundError:
    from airflow.dags.runtime_security_context import (
        sign_runtime_context,
        verify_runtime_signature,
    )


RUN_ISSUERS = frozenset({"console", "workspace", "mcp-infra", "airflow"})
RUN_PERMISSIONS = frozenset({"pipelines.run", "cartridges.execute"})
RUN_PURPOSE = "cartridge.run"
RUN_AUDIENCE = "airflow"
# Keys a stored entity_config.dag_params row may never carry: the scheduler owns them
# (entity_config columns and verified authority). conn_id/connection_id are reserved
# only there; console-built conf keeps them because the console resolves them for the
# caller's own workspace and the run's Vault reveal is bound to the admitted scope.
RESERVED_CONF_KEYS = frozenset(
    {
        "security_context",
        "tenant_id",
        "workspace_id",
        "cartridge_id",
        "conn_id",
        "connection_id",
        "triggered_by",
    }
)
MINTED_PERMISSIONS = ("cartridges.execute", "vault.secrets.reveal", "pipelines.run")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CARTRIDGE_RE = re.compile(r"[a-z0-9_]{1,64}")
_PRINCIPAL_MAX = 200


class RunAdmissionError(ValueError):
    pass


def admission_time(dag_run: Any) -> int | None:
    moment = getattr(dag_run, "queued_at", None) or getattr(dag_run, "start_date", None)
    return int(moment.timestamp()) if moment is not None else None


def _allowed_buckets() -> list[str]:
    buckets: list[str] = []
    for candidate in ("lakehouse", os.environ.get("MINIO_BUCKET"), os.environ.get("S3_BUCKET_NAME")):
        value = str(candidate or "").strip()
        if value and value not in buckets:
            buckets.append(value)
    return buckets


def _cartridge(value: Any) -> str:
    cartridge = str(value or "").strip()
    if not _CARTRIDGE_RE.fullmatch(cartridge):
        raise RunAdmissionError("run cartridge is invalid")
    return cartridge


def _scope(tenant_id: Any, workspace_id: Any) -> tuple[str, str]:
    tenant = str(tenant_id or "").strip().lower()
    workspace = str(workspace_id or "").strip().lower()
    if not _UUID_RE.fullmatch(tenant) or not _UUID_RE.fullmatch(workspace):
        raise RunAdmissionError("run needs tenant_id and workspace_id UUIDs")
    return tenant, workspace


def _principal(value: Any, fallback: str) -> str:
    text = str(value if value not in (None, "") else fallback).strip()
    return text[:_PRINCIPAL_MAX] or fallback


@dataclass(frozen=True)
class AdmittedRun:
    cartridge_id: str
    tenant_id: str
    workspace_id: str
    principal: str
    source: str

    @property
    def scope(self) -> str:
        return f"tenant_id={self.tenant_id}/workspace_id={self.workspace_id}/"

    def default_prefixes(self) -> list[str]:
        return [
            f"raw/{self.cartridge_id}/",
            f"silver/{self.cartridge_id}/{self.scope}",
            f"gold/{self.cartridge_id}/{self.scope}",
        ]

    def context(self, *, user_id: str, prefixes: list[str] | None = None) -> dict[str, Any]:
        actor = str(user_id or "").strip()
        if not actor:
            raise RunAdmissionError("minted context needs a user_id")
        return sign_runtime_context(
            {
                "trusted": True,
                "source": "airflow",
                "user_id": actor,
                "on_behalf_of": self.principal,
                "role": "admin",
                "workspace_role": "service",
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "permissions": list(MINTED_PERMISSIONS),
                "allowed_cartridges": [self.cartridge_id],
                "allowed_buckets": _allowed_buckets(),
                "allowed_prefixes": list(prefixes) if prefixes is not None else self.default_prefixes(),
            }
        )


def admit_run(
    conf: Mapping[str, Any] | None,
    *,
    cartridge_id: str,
    dag_run: Any,
    admitted_at: int | None = None,
) -> AdmittedRun:
    cartridge = _cartridge(cartridge_id)
    values = conf if isinstance(conf, Mapping) else {}
    ctx = values.get("security_context")
    if not isinstance(ctx, Mapping) or ctx.get("trusted") is not True:
        raise RunAdmissionError("signed run authority is required")
    source = str(ctx.get("source") or "")
    if source not in RUN_ISSUERS:
        raise RunAdmissionError("run authority issuer is not accepted")
    if ctx.get("purpose") not in (None, RUN_PURPOSE) or ctx.get("audience") not in (None, RUN_AUDIENCE):
        raise RunAdmissionError("run authority was issued for another purpose")
    clock = admitted_at if admitted_at is not None else admission_time(dag_run)
    try:
        verify_runtime_signature(ctx, now=clock)
    except ValueError as exc:
        raise RunAdmissionError(f"run authority rejected: {exc}") from None
    tenant, workspace = _scope(ctx.get("tenant_id"), ctx.get("workspace_id"))
    for key, expected in (("tenant_id", tenant), ("workspace_id", workspace)):
        supplied = str(values.get(key) or "").strip().lower()
        if supplied and supplied != expected:
            raise RunAdmissionError(f"conf {key} does not match run authority")
    allowed = {str(item).strip() for item in ctx.get("allowed_cartridges") or []}
    if "*" not in allowed and cartridge not in allowed:
        raise RunAdmissionError("cartridge is not allowed by run authority")
    if not RUN_PERMISSIONS & {str(item) for item in ctx.get("permissions") or []}:
        raise RunAdmissionError("run authority lacks a run permission")
    if "dag_id" in ctx or "dag_run_id" in ctx:
        bound = (str(ctx.get("dag_id") or ""), str(ctx.get("dag_run_id") or ""))
        actual = (str(getattr(dag_run, "dag_id", "") or ""), str(getattr(dag_run, "run_id", "") or ""))
        if not all(bound) or bound != actual:
            raise RunAdmissionError("run authority is bound to another run")
    return AdmittedRun(
        cartridge_id=cartridge,
        tenant_id=tenant,
        workspace_id=workspace,
        principal=_principal(ctx.get("user_id"), source),
        source=source,
    )


def service_run(cartridge_id: str, tenant_id: Any, workspace_id: Any, *, principal: str) -> AdmittedRun:
    """Authority read by the DAG itself from platform state (DB rows, Airflow Variables)."""
    tenant, workspace = _scope(tenant_id, workspace_id)
    actor = str(principal or "").strip()
    if not actor:
        raise RunAdmissionError("service run needs a principal")
    return AdmittedRun(
        cartridge_id=_cartridge(cartridge_id),
        tenant_id=tenant,
        workspace_id=workspace,
        principal=actor[:_PRINCIPAL_MAX],
        source="airflow",
    )


def strip_reserved(params: Any) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        return {}
    return {
        key: value
        for key, value in params.items()
        if isinstance(key, str) and key not in RESERVED_CONF_KEYS and not key.startswith("_")
    }


__all__ = [
    "AdmittedRun",
    "MINTED_PERMISSIONS",
    "RESERVED_CONF_KEYS",
    "RUN_AUDIENCE",
    "RUN_ISSUERS",
    "RUN_PERMISSIONS",
    "RUN_PURPOSE",
    "RunAdmissionError",
    "admission_time",
    "admit_run",
    "service_run",
    "strip_reserved",
]
