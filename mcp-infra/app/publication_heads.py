from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

import psycopg2

from app.config import settings
from app.storage_scope import canonical_storage_key, scoped_storage_allowed


def _dsn() -> str:
    if not settings.pg_gold_user or not settings.pg_gold_password:
        raise RuntimeError("Gold publication reader credentials are required")
    return (
        f"postgresql://{settings.pg_gold_user}:{settings.pg_gold_password}"
        f"@{settings.pg_gold_host}:{settings.pg_gold_port}/{settings.pg_gold_db}"
    )


def _scope(context: dict[str, Any] | None) -> tuple[str, str]:
    value = context or {}
    tenant = str(value.get("tenant_id") or "").strip()
    workspace = str(value.get("workspace_id") or "").strip()
    if not tenant or not workspace:
        raise PermissionError("published objects require tenant/workspace scope")
    return tenant, workspace


def _ref(uri: str) -> tuple[str, str]:
    parsed = urlsplit(str(uri or ""))
    if parsed.scheme not in {"s3", "gs"} or not parsed.netloc:
        return "", ""
    return parsed.netloc, parsed.path.lstrip("/")


def materialized_object(key: str) -> bool:
    return str(key or "").lstrip("/").split("/", 1)[0] in {"silver", "gold"}


def published_objects(context: dict[str, Any] | None, bucket: str) -> set[str]:
    heads = published_heads(context)
    refs = {_ref(str(value.get("object_uri") or "")) for value in heads.values()}
    return {key for object_bucket, key in refs if object_bucket == bucket and key}


def published_heads(context: dict[str, Any] | None) -> dict[tuple[str, str], dict]:
    tenant, workspace = _scope(context)
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        cur.execute("SELECT set_config('app.workspace_id',%s,true)", (workspace,))
        cur.execute(
            """
            SELECT h.dataset,h.layer,h.materialization_run_id::text,h.generation,
                   e.object_uri,e.object_checksum,e.row_count,e.catalog
              FROM omega_publication.dataset_publication_heads h
              JOIN omega_publication.materialization_runs r
                ON r.materialization_run_id=h.materialization_run_id
               AND r.tenant_id=h.tenant_id AND r.workspace_id=h.workspace_id
               AND r.dataset=h.dataset AND r.layer=h.layer
              JOIN omega_publication.materialization_receipts rec
                ON rec.materialization_run_id=h.materialization_run_id
               AND rec.tenant_id=h.tenant_id AND rec.workspace_id=h.workspace_id
               AND rec.dataset=h.dataset AND rec.layer=h.layer
               AND rec.generation=h.generation
              JOIN omega_publication.materialization_evidence e
                ON e.materialization_run_id=h.materialization_run_id
               AND e.tenant_id=h.tenant_id AND e.workspace_id=h.workspace_id
               AND e.dataset=h.dataset AND e.layer=h.layer
               AND e.object_uri=r.object_uri AND e.object_version=r.object_version
               AND e.object_checksum=r.object_checksum AND e.row_count=r.row_count
               AND e.schema_digest=r.schema_digest
               AND e.evidence_digest=r.evidence_digest
               AND rec.object_version=e.object_version
               AND rec.object_checksum=e.object_checksum
               AND rec.row_count=e.row_count
               AND rec.schema_digest=e.schema_digest
               AND rec.evidence_digest=e.evidence_digest
             WHERE h.tenant_id=%s AND h.workspace_id=%s
               AND r.status='published'
               AND r.object_uri IS NOT NULL
               AND r.object_checksum ~ '^[0-9a-f]{64}$'
            """,
            (tenant, workspace),
        )
        return {
            (str(row[0]), str(row[1])): {
                "run_id": str(row[2]),
                "generation": int(row[3]),
                "object_uri": row[4],
                "object_checksum": str(row[5]),
                "row_count": int(row[6] or 0),
                "catalog": row[7] or [],
            }
            for row in cur.fetchall()
        }


def publication_epoch(context: dict[str, Any] | None) -> str:
    heads = published_heads(context)
    payload = "\0".join(
        f"{dataset}|{layer}|{value['run_id']}|{value['generation']}"
        for (dataset, layer), value in sorted(heads.items())
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def published_dataset_names(context: dict[str, Any] | None) -> set[str]:
    try:
        return {dataset for dataset, _layer in published_heads(context)}
    except (PermissionError, RuntimeError):
        return set()


def scoped_semantic_source_name(
    base: str, context: dict[str, Any] | None, tenant: str, workspace: str
) -> str:
    if not tenant or not workspace:
        return base
    scope = {"tenant_id": tenant, "workspace_id": workspace}
    return (
        f"{base}:head:{publication_epoch(scope)}:tenant:{tenant}:workspace:{workspace}"
    )


def published_object(key: str, context: dict[str, Any] | None, bucket: str) -> bool:
    try:
        clean = canonical_storage_key(key)
    except ValueError:
        return False
    if not scoped_storage_allowed(clean, context, bucket=bucket):
        return False
    if not materialized_object(clean):
        return clean.split("/", 1)[0] == "raw"
    return clean in published_objects(context, bucket)


def published_object_checksum(
    key: str, context: dict[str, Any] | None, bucket: str
) -> str | None:
    try:
        clean = canonical_storage_key(key)
    except ValueError:
        return None
    if not scoped_storage_allowed(clean, context, bucket=bucket):
        return None
    for head in published_heads(context).values():
        object_bucket, object_key = _ref(str(head.get("object_uri") or ""))
        if object_bucket == bucket and object_key == clean:
            return str(head.get("object_checksum") or "") or None
    return None


def published_prefix(key: str, context: dict[str, Any] | None, bucket: str) -> bool:
    try:
        clean = canonical_storage_key(key)
    except ValueError:
        return False
    if not scoped_storage_allowed(clean, context, bucket=bucket):
        return False
    if not materialized_object(clean):
        return clean.split("/", 1)[0] == "raw"
    return any(
        value == clean.rstrip("/") or value.startswith(clean)
        for value in published_objects(context, bucket)
    )
