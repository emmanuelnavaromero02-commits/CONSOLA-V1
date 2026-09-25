from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import secrets
from typing import Any, Mapping

import psycopg2


VERSION = "hmac-sha256-v1"
PURPOSE = "dataset_refresh.admit"
_DIRECT_TTL = 300
_BRIDGE_TTL = 86_400
_FIELDS = {
    "source",
    "audience",
    "purpose",
    "dag_id",
    "dag_run_id",
    "tenant_id",
    "workspace_id",
    "cartridge_id",
    "seed_raw",
    "seed_dataset",
    "max_depth",
    "conf_digest",
    "jti",
    "_signed_at",
    "_expires_at",
    "_signature_version",
    "_signature",
}


def _conf_digest(conf: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in conf.items() if key != "security_context"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def build_dataset_refresh_admission(
    *,
    upstream_context: Mapping[str, Any],
    conf: Mapping[str, Any],
    dag_run_id: str,
) -> dict[str, Any]:
    try:
        from runtime_security_context import verify_runtime_signature
    except ModuleNotFoundError:
        from airflow.dags.runtime_security_context import verify_runtime_signature
    verify_runtime_signature(upstream_context)
    tenant_id = str(upstream_context.get("tenant_id") or "").strip()
    workspace_id = str(upstream_context.get("workspace_id") or "").strip()
    cartridge_id = str(conf.get("cartridge_id") or "").strip()
    allowed = {str(item) for item in upstream_context.get("allowed_cartridges") or []}
    permissions = {str(item) for item in upstream_context.get("permissions") or []}
    if (
        upstream_context.get("trusted") is not True
        or str(upstream_context.get("source") or "")
        not in {"airflow", "console", "mcp-infra", "workspace"}
        or "pipelines.run" not in permissions
        or not tenant_id
        or not workspace_id
        or not cartridge_id
        or ("*" not in allowed and cartridge_id not in allowed)
    ):
        raise ValueError("dataset refresh upstream authority mismatch")
    seed_raw = str(conf.get("seed_raw") or "")
    seed_dataset = str(conf.get("seed_dataset") or "")
    max_depth = int(conf.get("max_depth") or 10)
    envelope: dict[str, Any] = {
        "source": "airflow:file_ingest",
        "audience": "airflow",
        "purpose": PURPOSE,
        "dag_id": "dataset_refresh_chain",
        "dag_run_id": dag_run_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "cartridge_id": cartridge_id,
        "seed_raw": seed_raw,
        "seed_dataset": seed_dataset,
        "max_depth": int(max_depth),
        "jti": secrets.token_hex(32),
        "conf_digest": _conf_digest(conf),
        "_signed_at": int(time.time()),
        "_signature_version": VERSION,
    }
    envelope["_expires_at"] = int(envelope["_signed_at"]) + _BRIDGE_TTL
    envelope["_signature"] = hmac.new(
        _key(), _canonical(envelope), hashlib.sha256
    ).hexdigest()
    return envelope


def build_dataset_refresh_trigger(
    *,
    upstream_context: Mapping[str, Any],
    conf: Mapping[str, Any],
    source_dag_run_id: str,
    prefix: str,
) -> dict[str, Any]:
    source_run = str(source_dag_run_id or "").strip()
    clean_prefix = "".join(
        char for char in str(prefix or "") if char.isalnum() or char in "_-"
    )[:40]
    if not source_run or not clean_prefix:
        raise ValueError("dataset refresh source run is incomplete")
    target_digest = hashlib.sha256(
        _canonical({"source_dag_run_id": source_run, "conf": dict(conf)})
    ).hexdigest()[:32]
    dag_run_id = f"{clean_prefix}__dataset_refresh__{target_digest}"
    refresh_conf = dict(conf)
    refresh_conf["security_context"] = build_dataset_refresh_admission(
        upstream_context=upstream_context,
        conf=refresh_conf,
        dag_run_id=dag_run_id,
    )
    return {"dag_run_id": dag_run_id, "conf": refresh_conf}


def _canonical(value: Mapping[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "_signature"}
    return json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _key() -> bytes:
    value = str(os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(value) < 32:
        raise ValueError("dataset refresh admission key unavailable")
    return value.encode("utf-8")


def validate_dataset_refresh_admission(
    context: Mapping[str, Any],
    *,
    conf: Mapping[str, Any],
    run_id: str,
    postgres_dsn: str,
    now: int | None = None,
) -> tuple[str, str]:
    if not isinstance(context, dict) or set(context) != _FIELDS:
        raise ValueError("dataset refresh admission envelope mismatch")
    fixed = {
        "audience": "airflow",
        "purpose": PURPOSE,
        "dag_id": "dataset_refresh_chain",
        "dag_run_id": run_id,
        "tenant_id": str(conf.get("tenant_id") or "").strip(),
        "workspace_id": str(conf.get("workspace_id") or "").strip(),
        "cartridge_id": str(conf.get("cartridge_id") or "").strip(),
        "seed_raw": str(conf.get("seed_raw") or ""),
        "seed_dataset": str(conf.get("seed_dataset") or ""),
        "max_depth": int(conf.get("max_depth") or 10),
        "conf_digest": _conf_digest(conf),
        "_signature_version": VERSION,
    }
    if context.get("source") not in {"mcp-infra", "airflow:file_ingest"}:
        raise ValueError("dataset refresh admission issuer mismatch")
    if any(context.get(key) != value for key, value in fixed.items()):
        raise ValueError("dataset refresh admission binding mismatch")
    try:
        signed_at = int(context["_signed_at"])
        expires_at = int(context["_expires_at"])
        bytes.fromhex(str(context["jti"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("dataset refresh admission identity invalid") from exc
    clock = int(time.time() if now is None else now)
    if (
        len(str(context["jti"])) != 64
        or signed_at > clock + 30
        or expires_at - signed_at
        != (_DIRECT_TTL if context.get("source") == "mcp-infra" else _BRIDGE_TTL)
    ):
        raise ValueError("dataset refresh admission expired")
    expected = hmac.new(_key(), _canonical(context), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(context.get("_signature") or ""), expected):
        raise ValueError("dataset refresh admission signature mismatch")
    tenant, workspace = fixed["tenant_id"], fixed["workspace_id"]
    if not tenant or not workspace:
        raise ValueError("dataset refresh admission scope is incomplete")
    with psycopg2.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT consume_dataset_refresh_admission(%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)",
            (
                str(context["jti"]),
                tenant,
                workspace,
                run_id,
                fixed["cartridge_id"],
                fixed["conf_digest"],
                signed_at,
                expires_at,
            ),
        )
        if cur.fetchone()[0] is not True:
            raise ValueError("dataset refresh admission was not consumed")
    return tenant, workspace
