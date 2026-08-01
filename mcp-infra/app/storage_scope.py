from __future__ import annotations

import re
import unicodedata
from typing import Any


_SEGMENT = re.compile(r"^[A-Za-z0-9_.:=-]+$")
_GLOB_SEGMENT = re.compile(r"^[A-Za-z0-9_.:=*?-]+$")
_SCOPED_ROOTS = {"raw", "silver", "gold", "uploads"}


def canonical_storage_key(value: object, *, allow_glob: bool = False) -> str:
    raw = str(value or "").strip()
    if (
        not raw
        or raw.startswith("/")
        or "%" in raw
        or "\\" in raw
        or "//" in raw
        or unicodedata.normalize("NFKC", raw) != raw
        or not raw.isascii()
    ):
        raise ValueError("storage path is not canonical")
    if raw.endswith("/"):
        raw = raw[:-1]
    if not raw:
        raise ValueError("storage path is not canonical")
    parts = raw.split("/")
    segment = _GLOB_SEGMENT if allow_glob else _SEGMENT
    if any(
        not part or part in {".", ".."} or not segment.fullmatch(part) for part in parts
    ):
        raise ValueError("storage path is not canonical")
    return "/".join(parts)


def scoped_storage_key(
    value: object,
    context: dict[str, Any] | None,
    *,
    bucket: str | None = None,
    allow_glob: bool = False,
) -> str:
    key = canonical_storage_key(value, allow_glob=allow_glob)
    parts = key.split("/")
    if not parts or parts[0] not in _SCOPED_ROOTS:
        raise ValueError("storage root is not scoped")
    ctx = context if isinstance(context, dict) else {}
    tenant = str(ctx.get("tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or "").strip()
    if not tenant or not workspace:
        raise PermissionError("tenant/workspace storage scope required")
    scope_index = 2 if parts[0] == "uploads" else 3
    if len(parts) < scope_index + 2:
        raise PermissionError("tenant/workspace storage scope required")
    if parts[scope_index : scope_index + 2] != [
        f"tenant_id={tenant}",
        f"workspace_id={workspace}",
    ]:
        raise PermissionError("storage scope mismatch")
    markers = [
        part for part in parts if part.startswith(("tenant_id=", "workspace_id="))
    ]
    if markers != [f"tenant_id={tenant}", f"workspace_id={workspace}"]:
        raise PermissionError("storage scope mismatch")
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if parts[1] not in allowed and "*" not in allowed:
        raise PermissionError("storage cartridge mismatch")
    buckets = {
        str(item).strip()
        for item in (ctx.get("allowed_buckets") or [])
        if str(item).strip()
    }
    if bucket and buckets and bucket not in buckets:
        raise PermissionError("storage bucket mismatch")
    return key


def scoped_storage_allowed(
    value: object,
    context: dict[str, Any] | None,
    *,
    bucket: str | None = None,
) -> bool:
    try:
        scoped_storage_key(value, context, bucket=bucket)
        return True
    except (PermissionError, ValueError):
        return False
