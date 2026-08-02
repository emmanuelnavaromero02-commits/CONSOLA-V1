"""Build fresh, backend-owned security contexts for market extraction DAGs."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

try:
    from runtime_security_context import sign_runtime_context, verify_runtime_signature
except ModuleNotFoundError:  # package import in repository tests
    from airflow.dags.runtime_security_context import (
        sign_runtime_context,
        verify_runtime_signature,
    )


_SIGNATURE_FIELDS = {"_signature", "_signed_at", "_signature_version"}
_MARKET_ENTITIES = {
    "banxico": ("series_metadata", "series_observations"),
    "inegi": ("series_metadata", "series_observations"),
    "sec_edgar": ("company_metadata", "company_facts"),
}
_CARTRIDGE_RE = re.compile(r"^[a-z0-9_]+$")


def _allowed_buckets() -> list[str]:
    buckets: list[str] = []
    for candidate in (
        "lakehouse",
        os.environ.get("MINIO_BUCKET"),
        os.environ.get("S3_BUCKET_NAME"),
    ):
        value = str(candidate or "").strip()
        if value and value not in buckets:
            buckets.append(value)
    return buckets


def _allowed_prefixes(
    cartridge_id: str, tenant_id: str, workspace_id: str
) -> list[str]:
    scope = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
    prefixes = [
        f"raw/{cartridge_id}/{entity}/{scope}"
        for entity in _MARKET_ENTITIES[cartridge_id]
    ]
    prefixes.extend(f"{layer}/{cartridge_id}/{scope}" for layer in ("silver", "gold"))
    return prefixes


def security_context_from_conf(
    conf: Mapping[str, Any], cartridge_id: str
) -> dict[str, Any]:
    """Return a fresh signed context scoped to the extraction run."""
    if (
        not _CARTRIDGE_RE.fullmatch(cartridge_id)
        or cartridge_id not in _MARKET_ENTITIES
    ):
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
        verify_runtime_signature(supplied)
        allowed = {str(item) for item in supplied.get("allowed_cartridges") or []}
        if "*" not in allowed and cartridge_id not in allowed:
            raise ValueError("market cartridge not allowed by security_context")
        payload = {
            key: value
            for key, value in supplied.items()
            if key not in _SIGNATURE_FIELDS
        }
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
            "allowed_prefixes": _allowed_prefixes(
                cartridge_id, tenant_id, workspace_id
            ),
        }

    return sign_runtime_context(payload)
