"""HMAC v2, one-use verifier for scheduled monitor effects."""

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


PURPOSE = "mcp.scheduled_effect"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TTL = 300
_FIELDS = {
    "source",
    "audience",
    "purpose",
    "tool",
    "schedule_run_id",
    "fencing_token",
    "tenant_id",
    "workspace_id",
    "agent_id",
    "agent_run_id",
    "jti",
    "body_digest",
    "_signed_at",
    "_signature_version",
    "_signature",
}


def _canonical(value: Mapping[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "_signature"}
    return json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _key() -> str:
    key = str(os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < 32:
        raise ValueError("scheduled effect signing key unavailable")
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


def _consume(authority: Mapping[str, Any], signed_at: int) -> None:
    try:
        with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.tenant_id',%s,true),"
                "set_config('app.workspace_id',%s,true)",
                (authority["tenant_id"], authority["workspace_id"]),
            )
            cur.execute(
                """INSERT INTO runtime_hmac_nonces(
                       jti,tenant_id,workspace_id,run_id,audience,purpose,
                       body_digest,signed_at,expires_at,schedule_run_id,fencing_token)
                   VALUES(%s,%s::uuid,%s::uuid,%s,'mcp-infra',%s,%s,
                          to_timestamp(%s),to_timestamp(%s),%s,%s)""",
                (
                    authority["jti"],
                    authority["tenant_id"],
                    authority["workspace_id"],
                    str(authority["agent_run_id"]),
                    PURPOSE,
                    authority["body_digest"],
                    signed_at,
                    signed_at + _TTL,
                    authority["schedule_run_id"],
                    authority["fencing_token"],
                ),
            )
    except Exception as exc:
        raise ValueError("scheduled effect replay rejected") from exc


def validate_scheduled_effect_authority(
    authority: object, *, tool: str, args: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(authority, dict) or set(authority) != _FIELDS:
        raise ValueError("scheduled effect envelope mismatch")
    fixed = {
        "source": "console",
        "audience": "mcp-infra",
        "purpose": PURPOSE,
        "tool": tool,
        "tenant_id": context.get("tenant_id"),
        "workspace_id": context.get("workspace_id"),
        "agent_id": context.get("agent_id"),
        "agent_run_id": context.get("agent_run_id"),
        "_signature_version": "hmac-sha256-v2",
    }
    if any(authority.get(key) != value for key, value in fixed.items()):
        raise ValueError("scheduled effect binding mismatch")
    unsigned_args = {
        key: value for key, value in args.items() if key != "effect_authority"
    }
    digest = hashlib.sha256(
        json.dumps(
            {"tool": tool, "args": unsigned_args},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if authority.get("body_digest") != digest or not _HEX.fullmatch(
        str(authority.get("jti") or "")
    ):
        raise ValueError("scheduled effect body mismatch")
    try:
        signed_at = int(authority.get("_signed_at"))
        int(authority.get("schedule_run_id"))
        int(authority.get("fencing_token"))
    except (TypeError, ValueError) as exc:
        raise ValueError("scheduled effect identity invalid") from exc
    now = int(time.time())
    if signed_at > now + 30 or now - signed_at > _TTL:
        raise ValueError("scheduled effect authority expired")
    expected = hmac.new(
        _key().encode(), _canonical(authority), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(str(authority.get("_signature") or ""), expected):
        raise ValueError("scheduled effect signature mismatch")
    _consume(authority, signed_at)
    return authority
