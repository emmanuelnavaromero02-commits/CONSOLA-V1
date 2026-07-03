"""Scoped data-platform read helpers.

These helpers keep Bronze/Silver/Gold read surfaces tenant/workspace aware and
provide the small singleflight cache used by viewer/catalog endpoints.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from copy import deepcopy
from typing import Any

from fastapi import HTTPException

from app.services.security_context import build_security_context


BRONZE_LOGICAL_READ_PARQUET_CALL_RE = re.compile(
    r"\bread_parquet\s*\(\s*(['\"])(raw/[A-Za-z0-9_./=-]+)\1\s*\)",
    re.IGNORECASE,
)
BRONZE_LOGICAL_READ_PARQUET_PATH_RE = re.compile(
    r"(\bread_parquet\s*\(\s*['\"])(raw/[A-Za-z0-9_./=-]+)(['\"])",
    re.IGNORECASE,
)
BRONZE_READ_PARQUET_SOURCE_RE = re.compile(
    r"\bread_parquet\s*\(\s*(['\"])((?:s3://(?:\{bucket\}|[A-Za-z0-9_.:-]+)/)?raw/[^'\"\\]+)\1",
    re.IGNORECASE,
)
SAFE_BRONZE_SOURCE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_.:-]+")

SCOPED_READ_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
SCOPED_READ_CACHE_LOCKS: dict[tuple[Any, ...], asyncio.Lock] = {}


def bronze_source_from_reader_path(path: str) -> str | None:
    value = str(path or "").strip().strip("/")
    if value.startswith("s3://"):
        _, _, rest = value[5:].partition("/")
        value = rest.strip("/")
    parts = [part for part in value.split("/") if part]
    if len(parts) < 3 or parts[0] != "raw":
        return None
    if any(part in {".", ".."} or part.startswith("..") for part in parts):
        return None
    cartridge = parts[1]
    entity = parts[2]
    if entity.startswith("tenant_id="):
        if len(parts) < 5 or not parts[3].startswith("workspace_id="):
            return None
        entity = parts[4]
    if (
        not SAFE_BRONZE_SOURCE_SEGMENT_RE.fullmatch(cartridge)
        or not SAFE_BRONZE_SOURCE_SEGMENT_RE.fullmatch(entity)
        or "=" in cartridge
        or "=" in entity
        or "*" in entity
    ):
        return None
    return f"raw/{cartridge}/{entity}"


def infer_bronze_sources_from_sql(sql: str) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for match in BRONZE_READ_PARQUET_SOURCE_RE.finditer(sql or ""):
        source = bronze_source_from_reader_path(match.group(2))
        if source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources


def merge_declared_and_inferred_bronze_sources(
    declared: Any,
    sql: str,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    items = declared if isinstance(declared, list) else []
    for source in [*items, *infer_bronze_sources_from_sql(sql)]:
        value = str(source or "").strip()
        if value and value not in seen:
            seen.add(value)
            merged.append(value)
    return merged


def workspace_scope_from_user(user: dict | None) -> tuple[str, str]:
    ctx = build_security_context(user)
    tenant_id = str(
        ctx.get("tenant_id")
        or (user or {}).get("active_tenant_id")
        or (user or {}).get("tenant_id")
        or ""
    ).strip()
    workspace_id = str(
        ctx.get("workspace_id")
        or (user or {}).get("active_workspace_id")
        or (user or {}).get("workspace_id")
        or ""
    ).strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(400, "Bronze query requires tenant/workspace scope")
    return tenant_id, workspace_id


def scoped_read_cache_ttl() -> float:
    raw = os.environ.get("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "20")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 20.0
    return max(0.0, min(value, 300.0))


def scoped_cache_identity(user: dict | None) -> tuple[Any, ...]:
    ctx = build_security_context(user)
    tenant_id = str(
        ctx.get("tenant_id")
        or (user or {}).get("active_tenant_id")
        or (user or {}).get("tenant_id")
        or ""
    ).strip()
    workspace_id = str(
        ctx.get("workspace_id")
        or (user or {}).get("active_workspace_id")
        or (user or {}).get("workspace_id")
        or ""
    ).strip()
    allowed = tuple(
        sorted(
            str(item).strip()
            for item in (ctx.get("allowed_cartridges") or [])
            if str(item).strip()
        )
    )
    return (
        tenant_id,
        workspace_id,
        str(ctx.get("role") or (user or {}).get("role") or "").strip(),
        str((user or {}).get("id") or ctx.get("sub") or "").strip(),
        allowed,
    )


def scoped_read_cache_key(
    namespace: str, user: dict | None, *parts: Any
) -> tuple[Any, ...]:
    return (namespace, scoped_cache_identity(user), parts)


def scoped_read_cache_get(
    namespace: str, user: dict | None, *parts: Any
) -> Any | None:
    ttl = scoped_read_cache_ttl()
    if ttl <= 0:
        return None
    key = scoped_read_cache_key(namespace, user, *parts)
    cached = SCOPED_READ_CACHE.get(key)
    if not cached:
        return None
    expires_at, value = cached
    if expires_at <= time.monotonic():
        SCOPED_READ_CACHE.pop(key, None)
        return None
    return deepcopy(value)


def scoped_read_cache_set(
    namespace: str, user: dict | None, value: Any, *parts: Any
) -> Any:
    ttl = scoped_read_cache_ttl()
    if ttl <= 0:
        return value
    key = scoped_read_cache_key(namespace, user, *parts)
    SCOPED_READ_CACHE[key] = (time.monotonic() + ttl, deepcopy(value))
    return value


async def scoped_read_cache_get_or_set(
    namespace: str, user: dict | None, parts: tuple[Any, ...], loader
) -> Any:
    cached = scoped_read_cache_get(namespace, user, *parts)
    if cached is not None:
        return cached
    ttl = scoped_read_cache_ttl()
    if ttl <= 0:
        return await loader()
    key = scoped_read_cache_key(namespace, user, *parts)
    lock = SCOPED_READ_CACHE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = scoped_read_cache_get(namespace, user, *parts)
        if cached is not None:
            return cached
        return scoped_read_cache_set(namespace, user, await loader(), *parts)


def scoped_read_cache_invalidate(namespace: str, user: dict | None = None) -> None:
    if user is None:
        doomed = [key for key in SCOPED_READ_CACHE if key and key[0] == namespace]
    else:
        identity = scoped_cache_identity(user)
        doomed = [
            key
            for key in SCOPED_READ_CACHE
            if key and key[0] == namespace and len(key) > 1 and key[1] == identity
        ]
    for key in doomed:
        SCOPED_READ_CACHE.pop(key, None)
        SCOPED_READ_CACHE_LOCKS.pop(key, None)


def bronze_bucket_name() -> str:
    return (
        os.environ.get("S3_BUCKET_NAME")
        or os.environ.get("MINIO_BUCKET")
        or "lakehouse"
    ).strip()


def scoped_bronze_s3_path(logical_path: str, user: dict | None) -> str:
    path = str(logical_path or "").strip().strip("/")
    parts = path.split("/")
    if (
        len(parts) < 3
        or parts[0] != "raw"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise HTTPException(400, "Invalid bronze source path")
    if "tenant_id=" in path or "workspace_id=" in path:
        raise HTTPException(
            400, "Bronze logical paths must omit tenant/workspace partitions"
        )
    tenant_id, workspace_id = workspace_scope_from_user(user)
    return (
        f"s3://{bronze_bucket_name()}/{path}/"
        f"tenant_id={tenant_id}/workspace_id={workspace_id}/**/*.parquet"
    )


def rewrite_bronze_logical_paths(sql: str, user: dict | None) -> str:
    """Allow the UI to submit logical raw paths while preserving scoped S3 reads."""
    if "read_parquet" not in (sql or "").lower():
        return sql

    def replace_call(match: re.Match[str]) -> str:
        scoped_path = scoped_bronze_s3_path(match.group(2), user)
        quote = match.group(1)
        return (
            f"read_parquet({quote}{scoped_path}{quote}, "
            "hive_partitioning=true, union_by_name=true)"
        )

    rewritten = BRONZE_LOGICAL_READ_PARQUET_CALL_RE.sub(replace_call, sql)

    def replace_path(match: re.Match[str]) -> str:
        return (
            f"{match.group(1)}"
            f"{scoped_bronze_s3_path(match.group(2), user)}"
            f"{match.group(3)}"
        )

    return BRONZE_LOGICAL_READ_PARQUET_PATH_RE.sub(replace_path, rewritten)
