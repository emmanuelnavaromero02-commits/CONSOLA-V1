"""Purpose-bound HMAC contexts emitted by automatic Airflow runtimes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any


MATERIALIZE_PURPOSE = "refinement.mcp.materialize"
SIGNATURE_VERSION = "hmac-sha256-v1"
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
    now: int | None = None,
) -> dict[str, Any]:
    """Build a fresh context tied to one exact materialize request."""
    tenant = _required(tenant_id, "tenant_id")
    workspace = _required(workspace_id, "workspace_id")
    cartridge = _required(cartridge_id, "cartridge_id")
    dataset = _required(dataset_name, "dataset_name")
    scope = f"tenant_id={tenant}/workspace_id={workspace}/"
    context: dict[str, Any] = {
        "trusted": True,
        "source": "airflow",
        "audience": "refinement",
        "purpose": MATERIALIZE_PURPOSE,
        "tool": "materialize",
        "dataset": dataset,
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
    return sign_runtime_context(context, now=now)


def sign_runtime_context(
    payload: Mapping[str, Any],
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Sign one reduced server-owned context with the shared primitive."""
    context = {
        key: value
        for key, value in payload.items()
        if key not in {"_signature", "_signed_at", "_signature_version"}
    }
    context["_signed_at"] = int(time.time() if now is None else now)
    context["_signature_version"] = SIGNATURE_VERSION
    context["_signature"] = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical(context),
        hashlib.sha256,
    ).hexdigest()
    return context


def verify_runtime_signature(
    context: Mapping[str, Any],
    *,
    now: int | None = None,
) -> None:
    """Verify a delegated signed context before reducing and re-signing it."""
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
