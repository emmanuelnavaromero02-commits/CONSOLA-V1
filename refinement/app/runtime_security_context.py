"""Strict verifier for purpose-bound Airflow materialization contexts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any


MATERIALIZE_PURPOSE = "refinement.mcp.materialize"
SIGNATURE_VERSION = "hmac-sha256-v2"
_TTL_SECONDS = 300
_FUTURE_SKEW_SECONDS = 30
_MIN_KEY_LENGTH = 32
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_EXACT_FIELDS = {
    "trusted",
    "source",
    "audience",
    "purpose",
    "tool",
    "dataset",
    "run_id",
    "jti",
    "body_digest",
    "user_id",
    "role",
    "workspace_role",
    "tenant_id",
    "workspace_id",
    "permissions",
    "allowed_cartridges",
    "allowed_prefixes",
    "_signed_at",
    "_signature_version",
    "_signature",
}


def _signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _MIN_KEY_LENGTH:
        raise ValueError("runtime signing key unavailable")
    for name, value in os.environ.items():
        if name != "INTERNAL_API_KEY" and not name.startswith("INTERNAL_API_KEY_"):
            continue
        transport = str(value or "").strip()
        if transport and hmac.compare_digest(key, transport):
            raise ValueError("runtime signing key is not isolated")
    return key


def _canonical(context: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in context.items() if key != "_signature"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _exact_request_binding(context: Mapping[str, Any], body: Mapping[str, Any]) -> None:
    args = body.get("args") if isinstance(body.get("args"), Mapping) else {}
    if set(context) != _EXACT_FIELDS:
        raise ValueError("runtime context envelope mismatch")
    request_fields = set(body) - {"security_context", "_verified_internal_service"}
    if request_fields != {"tool", "args"}:
        raise ValueError("runtime context request fields mismatch")
    if set(args) != {"name"}:
        raise ValueError("runtime context request fields mismatch")
    if context.get("tool") != "materialize" or body.get("tool") != "materialize":
        raise ValueError("runtime context tool mismatch")
    dataset = str(context.get("dataset") or "").strip()
    if not dataset or dataset != str(args.get("name") or "").strip():
        raise ValueError("runtime context dataset mismatch")
    run_id = str(context.get("run_id") or "").strip()
    if not run_id or len(run_id) > 250:
        raise ValueError("runtime context run mismatch")
    if not _HEX_64.fullmatch(str(context.get("jti") or "")):
        raise ValueError("runtime context jti mismatch")
    request_digest = hashlib.sha256(
        json.dumps(
            {"args": {"name": dataset}, "tool": "materialize"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if context.get("body_digest") != request_digest:
        raise ValueError("runtime context body mismatch")
    if context.get("permissions") != ["datasets.read", "datasets.write"]:
        raise ValueError("runtime context permissions mismatch")
    cartridges = context.get("allowed_cartridges")
    if not isinstance(cartridges, list) or len(cartridges) != 1 or not cartridges[0]:
        raise ValueError("runtime context cartridge scope mismatch")
    cartridge = str(cartridges[0]).strip()
    tenant = str(context.get("tenant_id") or "").strip()
    workspace = str(context.get("workspace_id") or "").strip()
    if not tenant:
        raise ValueError("runtime context tenant scope missing")
    if not workspace:
        raise ValueError("runtime context workspace scope missing")
    scope = f"tenant_id={tenant}/workspace_id={workspace}/"
    expected_prefixes = [
        f"raw/{cartridge}/",
        f"silver/{cartridge}/{scope}",
        f"gold/{cartridge}/{scope}",
        f"uploads/{cartridge}/{scope}",
        f"cartridges/{cartridge}/",
    ]
    if context.get("allowed_prefixes") != expected_prefixes:
        raise ValueError("runtime context prefixes mismatch")
    if context.get("user_id") != "airflow:dataset_refresh_chain":
        raise ValueError("runtime context actor mismatch")
    if context.get("role") != "admin" or context.get("workspace_role") != "service":
        raise ValueError("runtime context role mismatch")


def _consume_runtime_jti(context: Mapping[str, Any], signed_at: int) -> None:
    import psycopg2

    dsn = (os.environ.get("DATABASE_URL") or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    if not dsn:
        raise ValueError("runtime replay ledger unavailable")
    tenant = str(context["tenant_id"])
    workspace = str(context["workspace_id"])
    try:
        with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.tenant_id',%s,true), "
                "set_config('app.workspace_id',%s,true)",
                (tenant, workspace),
            )
            cursor.execute(
                """INSERT INTO runtime_hmac_nonces (
                       jti, tenant_id, workspace_id, run_id, audience, purpose,
                       body_digest, signed_at, expires_at
                   ) VALUES (%s,%s::uuid,%s::uuid,%s,%s,%s,%s,to_timestamp(%s),to_timestamp(%s))""",
                (
                    context["jti"],
                    tenant,
                    workspace,
                    context["run_id"],
                    context["audience"],
                    context["purpose"],
                    context["body_digest"],
                    signed_at,
                    signed_at + _TTL_SECONDS,
                ),
            )
    except Exception as exc:
        raise ValueError("runtime context replay rejected") from exc


def validate_runtime_context(
    context: Mapping[str, Any],
    *,
    body: Mapping[str, Any],
    internal_service: str,
    now: int | None = None,
    consume: bool = False,
) -> None:
    """Reject any unsigned, stale, replayed, or over-broad Airflow context."""
    if internal_service != "airflow" or context.get("source") != "airflow":
        raise ValueError("runtime context source mismatch")
    if context.get("audience") != "refinement":
        raise ValueError("runtime context audience mismatch")
    if context.get("purpose") != MATERIALIZE_PURPOSE:
        raise ValueError("runtime context purpose mismatch")
    if context.get("_signature_version") != SIGNATURE_VERSION:
        raise ValueError("runtime context signature version mismatch")
    signature = str(context.get("_signature") or "")
    if not signature:
        raise ValueError("runtime context signature missing")
    try:
        signed_at = int(context.get("_signed_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime context signed_at missing") from exc
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
    _exact_request_binding(context, body)
    if consume:
        _consume_runtime_jti(context, signed_at)
