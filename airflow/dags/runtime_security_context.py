from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any


MATERIALIZE_PURPOSE = "refinement.mcp.materialize"
PIPELINE_RUN_PURPOSE = "mcp.pipeline_run_save"
SIGNATURE_VERSION = "hmac-sha256-v1"
MATERIALIZE_SIGNATURE_VERSION = "hmac-sha256-v2"
_MIN_KEY_LENGTH = 32
_TTL_SECONDS = 300
_FUTURE_SKEW_SECONDS = 30


def _signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _MIN_KEY_LENGTH:
        raise RuntimeError("SECURITY_CONTEXT_SIGNING_KEY is required")
    for name, value in os.environ.items():
        if name != "INTERNAL_API_KEY" and not name.startswith("INTERNAL_API_KEY_"):
            continue
        transport = str(value or "").strip()
        if transport and hmac.compare_digest(key, transport):
            raise RuntimeError(
                f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {name}"
            )
    return key


def _canonical(context: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in context.items() if key != "_signature"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _required(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def build_materialize_context(
    *,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str,
    dataset_name: str,
    run_id: str,
    now: int | None = None,
) -> dict[str, Any]:
    tenant = _required(tenant_id, "tenant_id")
    workspace = _required(workspace_id, "workspace_id")
    cartridge = _required(cartridge_id, "cartridge_id")
    dataset = _required(dataset_name, "dataset_name")
    runtime_run = _required(run_id, "run_id")
    scope = f"tenant_id={tenant}/workspace_id={workspace}/"
    context: dict[str, Any] = {
        "trusted": True,
        "source": "airflow",
        "audience": "refinement",
        "purpose": MATERIALIZE_PURPOSE,
        "tool": "materialize",
        "dataset": dataset,
        "run_id": runtime_run,
        "jti": secrets.token_hex(32),
        "body_digest": hashlib.sha256(
            json.dumps(
                {"args": {"name": dataset}, "tool": "materialize"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "user_id": "airflow:dataset_refresh_chain",
        "role": "admin",
        "workspace_role": "service",
        "tenant_id": tenant,
        "workspace_id": workspace,
        "permissions": ["datasets.read", "datasets.write"],
        "allowed_cartridges": [cartridge],
        "allowed_prefixes": [
            f"raw/{cartridge}/",
            f"silver/{cartridge}/{scope}",
            f"gold/{cartridge}/{scope}",
            f"uploads/{cartridge}/{scope}",
            f"cartridges/{cartridge}/",
        ],
    }
    return _sign_context(context, version=MATERIALIZE_SIGNATURE_VERSION, now=now)


def build_pipeline_run_context(
    args: Mapping[str, Any], *, now: int | None = None
) -> dict[str, Any]:
    required = {"run_id", "dag_id", "cartridge_id", "entity", "status"}
    if not required.issubset(args) or any(
        not str(args[key] or "").strip() for key in required
    ):
        raise ValueError("pipeline telemetry identity is incomplete")
    cartridge = _required(args["cartridge_id"], "cartridge_id")
    tenant = str(args.get("tenant_id") or "").strip()
    workspace = str(args.get("workspace_id") or "").strip()
    if cartridge != "platform" and (not tenant or not workspace):
        raise ValueError("pipeline telemetry scope is incomplete")
    body = {"args": dict(args), "tool": "pipeline_run_save"}
    context: dict[str, Any] = {
        "trusted": True,
        "source": "airflow",
        "audience": "mcp-infra",
        "purpose": PIPELINE_RUN_PURPOSE,
        "tool": "pipeline_run_save",
        "run_id": _required(args["run_id"], "run_id"),
        "dag_id": _required(args["dag_id"], "dag_id"),
        "jti": secrets.token_hex(32),
        "body_digest": hashlib.sha256(
            json.dumps(
                body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest(),
        "user_id": f"airflow:{args['dag_id']}",
        "role": "service",
        "workspace_role": "service",
        "tenant_id": tenant,
        "workspace_id": workspace,
        "permissions": ["pipelines.write"],
        "allowed_cartridges": [cartridge],
    }
    return _sign_context(context, version=MATERIALIZE_SIGNATURE_VERSION, now=now)


def _sign_context(
    payload: Mapping[str, Any], *, version: str, now: int | None = None
) -> dict[str, Any]:
    context = {
        key: value
        for key, value in payload.items()
        if key not in {"_signature", "_signed_at", "_signature_version"}
    }
    context["_signed_at"] = int(time.time() if now is None else now)
    context["_signature_version"] = version
    context["_signature"] = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical(context),
        hashlib.sha256,
    ).hexdigest()
    return context


def sign_runtime_context(
    payload: Mapping[str, Any],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    return _sign_context(payload, version=SIGNATURE_VERSION, now=now)


def verify_runtime_signature(
    context: Mapping[str, Any],
    *,
    now: int | None = None,
) -> None:
    if context.get("_signature_version") != SIGNATURE_VERSION:
        raise ValueError("unsupported runtime context signature version")
    signature = str(context.get("_signature") or "")
    if not signature:
        raise ValueError("runtime context signature is required")
    try:
        signed_at = int(context.get("_signed_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime context signed_at is required") from exc
    clock = int(time.time() if now is None else now)
    if signed_at > clock + _FUTURE_SKEW_SECONDS or clock - signed_at > _TTL_SECONDS:
        raise ValueError("runtime context signature expired")
    expected = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical(context),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("runtime context signature mismatch")
