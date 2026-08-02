from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Mapping


VERSION = "hmac-sha256-v1"
PURPOSE = "dataset_refresh.admit"
AUDIENCE = "airflow"


def _canonical(value: Mapping[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "_signature"}
    return json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _conf_digest(conf: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in conf.items() if key != "security_context"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _key() -> bytes:
    value = str(os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(value) < 32:
        raise ValueError("dataset refresh admission key unavailable")
    return value.encode("utf-8")


def build_dataset_refresh_admission(
    context: Mapping[str, Any], conf: Mapping[str, Any], dag_run_id: str
) -> dict[str, Any]:
    tenant = str(context.get("tenant_id") or "").strip()
    workspace = str(context.get("workspace_id") or "").strip()
    cartridge = str(conf.get("cartridge_id") or "").strip()
    allowed = {str(item) for item in context.get("allowed_cartridges") or []}
    permissions = {str(item) for item in context.get("permissions") or []}
    if (
        context.get("trusted") is not True
        or str(context.get("source") or "")
        not in {"airflow", "console", "mcp-infra", "workspace"}
        or "pipelines.run" not in permissions
        or ("*" not in allowed and cartridge not in allowed)
        or not tenant
        or not workspace
        or not cartridge
        or not dag_run_id
    ):
        raise ValueError("dataset refresh admission scope is incomplete")
    envelope: dict[str, Any] = {
        "source": "mcp-infra",
        "audience": AUDIENCE,
        "purpose": PURPOSE,
        "dag_id": "dataset_refresh_chain",
        "dag_run_id": dag_run_id,
        "tenant_id": tenant,
        "workspace_id": workspace,
        "cartridge_id": cartridge,
        "seed_raw": str(conf.get("seed_raw") or ""),
        "seed_dataset": str(conf.get("seed_dataset") or ""),
        "max_depth": int(conf.get("max_depth") or 10),
        "conf_digest": _conf_digest(conf),
        "jti": secrets.token_hex(32),
        "_signed_at": int(time.time()),
        "_signature_version": VERSION,
    }
    envelope["_expires_at"] = int(envelope["_signed_at"]) + 300
    envelope["_signature"] = hmac.new(
        _key(), _canonical(envelope), hashlib.sha256
    ).hexdigest()
    return envelope
