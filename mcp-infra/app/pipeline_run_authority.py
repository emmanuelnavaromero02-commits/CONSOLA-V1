"""Strict one-use authority for Airflow pipeline telemetry."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any

import psycopg2


PURPOSE = "mcp.pipeline_run_save"
VERSION = "hmac-sha256-v2"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TTL = 300
_FIELDS = {
    "trusted",
    "source",
    "audience",
    "purpose",
    "tool",
    "run_id",
    "dag_id",
    "jti",
    "body_digest",
    "user_id",
    "role",
    "workspace_role",
    "tenant_id",
    "workspace_id",
    "permissions",
    "allowed_cartridges",
    "_signed_at",
    "_signature_version",
    "_signature",
}


def _canonical(value: Mapping[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "_signature"}
    return json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _key() -> str:
    key = str(os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < 32:
        raise ValueError("telemetry signing key unavailable")
    return key


def _dsn() -> str:
    value = str(os.environ.get("DATABASE_URL") or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    if value:
        return value
    from app.config import settings

    return (
        f"postgresql://{settings.pg_user}:{settings.pg_password}@"
        f"{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
    )


def _consume(context: Mapping[str, Any], signed_at: int) -> None:
    tenant = str(context.get("tenant_id") or "").strip() or None
    workspace = str(context.get("workspace_id") or "").strip() or None
    try:
        with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id',%s,true),"
                "set_config('app.workspace_id',%s,true)",
                (tenant or "", workspace or ""),
            )
            cur.execute(
                """INSERT INTO runtime_hmac_nonces(
                       jti,tenant_id,workspace_id,run_id,audience,purpose,
                       body_digest,signed_at,expires_at,dag_id,cartridge_id)
                   VALUES(%s,%s::uuid,%s::uuid,%s,'mcp-infra',%s,%s,
                          to_timestamp(%s),to_timestamp(%s),%s,%s)""",
                (
                    context["jti"],
                    tenant,
                    workspace,
                    context["run_id"],
                    PURPOSE,
                    context["body_digest"],
                    signed_at,
                    signed_at + _TTL,
                    context["dag_id"],
                    context["allowed_cartridges"][0],
                ),
            )
    except Exception as exc:
        raise ValueError("pipeline telemetry replay rejected") from exc


def validate_pipeline_run_authority(
    context: object, *, body: Mapping[str, Any], internal_service: str, consume: bool
) -> dict[str, Any]:
    if not isinstance(context, dict) or set(context) != _FIELDS:
        raise ValueError("pipeline telemetry envelope mismatch")
    args = body.get("args")
    if internal_service != "airflow" or not isinstance(args, dict):
        raise ValueError("pipeline telemetry source mismatch")
    fixed = {
        "trusted": True,
        "source": "airflow",
        "audience": "mcp-infra",
        "purpose": PURPOSE,
        "tool": "pipeline_run_save",
        "role": "service",
        "workspace_role": "service",
        "permissions": ["pipelines.write"],
        "_signature_version": VERSION,
    }
    if any(context.get(key) != value for key, value in fixed.items()):
        raise ValueError("pipeline telemetry authority mismatch")
    required = {"run_id", "dag_id", "cartridge_id", "entity", "status"}
    if not required.issubset(args):
        raise ValueError("pipeline telemetry body incomplete")
    cartridge = str(args.get("cartridge_id") or "").strip()
    tenant = str(args.get("tenant_id") or "").strip()
    workspace = str(args.get("workspace_id") or "").strip()
    if cartridge != "platform" and (not tenant or not workspace):
        raise ValueError("pipeline telemetry scope missing")
    expected = {
        "run_id": str(args.get("run_id") or "").strip(),
        "dag_id": str(args.get("dag_id") or "").strip(),
        "tenant_id": tenant,
        "workspace_id": workspace,
        "allowed_cartridges": [cartridge],
        "user_id": f"airflow:{args.get('dag_id')}",
    }
    if any(context.get(key) != value for key, value in expected.items()):
        raise ValueError("pipeline telemetry scope mismatch")
    digest = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    if context.get("body_digest") != digest or not _HEX.fullmatch(
        str(context.get("jti") or "")
    ):
        raise ValueError("pipeline telemetry body mismatch")
    try:
        signed_at = int(context.get("_signed_at"))
    except (TypeError, ValueError) as exc:
        raise ValueError("pipeline telemetry timestamp missing") from exc
    now = int(time.time())
    if signed_at > now + 30 or now - signed_at > _TTL:
        raise ValueError("pipeline telemetry signature expired")
    signature = str(context.get("_signature") or "")
    expected_signature = hmac.new(
        _key().encode(), _canonical(context), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("pipeline telemetry signature mismatch")
    if consume:
        _consume(context, signed_at)
    return context
