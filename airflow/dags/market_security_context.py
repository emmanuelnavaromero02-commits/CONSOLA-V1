"""Build fresh, backend-owned security contexts for market extraction DAGs."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any


_SIGNATURE_FIELDS = {"_signature", "_signed_at", "_signature_version"}
_SIGNATURE_VERSION = "hmac-sha256-v1"
_MARKET_ENTITIES = {
    "banxico": ("series_metadata", "series_observations"),
    "inegi": ("series_metadata", "series_observations"),
    "sec_edgar": ("company_metadata", "company_facts"),
}
_CARTRIDGE_RE = re.compile(r"^[a-z0-9_]+$")


def _signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < 32:
        raise RuntimeError("SECURITY_CONTEXT_SIGNING_KEY is required")
    for name, value in os.environ.items():
        if (
            (name == "INTERNAL_API_KEY" or name.startswith("INTERNAL_API_KEY_"))
            and isinstance(value, str)
            and value.strip()
            and hmac.compare_digest(key, value.strip())
        ):
            raise RuntimeError(f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {name}")
    return key


def _canonical(payload: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "_signature"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _allowed_buckets() -> list[str]:
    buckets: list[str] = []
    for candidate in ("lakehouse", os.environ.get("MINIO_BUCKET"), os.environ.get("S3_BUCKET_NAME")):
        value = str(candidate or "").strip()
        if value and value not in buckets:
            buckets.append(value)
    return buckets


def _allowed_prefixes(cartridge_id: str, tenant_id: str, workspace_id: str) -> list[str]:
    scope = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
    prefixes = [
        f"raw/{cartridge_id}/{entity}/{scope}"
        for entity in _MARKET_ENTITIES[cartridge_id]
    ]
    prefixes.extend(
        f"{layer}/{cartridge_id}/{scope}"
        for layer in ("silver", "gold")
    )
    return prefixes


def security_context_from_conf(conf: Mapping[str, Any], cartridge_id: str) -> dict[str, Any]:
    """Return a fresh signed context scoped to the extraction run."""
    if not _CARTRIDGE_RE.fullmatch(cartridge_id) or cartridge_id not in _MARKET_ENTITIES:
        raise ValueError("unsupported market cartridge")
    tenant_id = str(conf.get("tenant_id") or "").strip()
    workspace_id = str(conf.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required")

    supplied = conf.get("security_context")
    if supplied is not None:
        if not isinstance(supplied, Mapping) or supplied.get("trusted") is not True:
            raise ValueError("trusted security_context is required when supplied")
        if str(supplied.get("tenant_id") or "") != tenant_id:
            raise ValueError("security_context tenant mismatch")
        if str(supplied.get("workspace_id") or "") != workspace_id:
            raise ValueError("security_context workspace mismatch")
        allowed = {str(item) for item in supplied.get("allowed_cartridges") or []}
        if "*" not in allowed and cartridge_id not in allowed:
            raise ValueError("market cartridge not allowed by security_context")
        payload = {key: value for key, value in supplied.items() if key not in _SIGNATURE_FIELDS}
    else:
        payload = {
            "trusted": True,
            "source": "console",
            "user_id": "airflow:market-scheduler",
            "role": "admin",
            "workspace_role": "service",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "permissions": ["cartridges.execute", "vault.secrets.reveal"],
            "allowed_cartridges": [cartridge_id],
            "allowed_buckets": _allowed_buckets(),
            "allowed_prefixes": _allowed_prefixes(cartridge_id, tenant_id, workspace_id),
        }

    payload["_signed_at"] = int(time.time())
    payload["_signature_version"] = _SIGNATURE_VERSION
    payload["_signature"] = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical(payload),
        hashlib.sha256,
    ).hexdigest()
    return payload
