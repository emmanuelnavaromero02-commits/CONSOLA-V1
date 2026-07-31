from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

import psycopg2

from app.config import settings


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
                   r.object_uri,r.row_count,e.catalog
              FROM omega_publication.dataset_publication_heads h
              JOIN omega_publication.materialization_runs r
                ON r.materialization_run_id=h.materialization_run_id
              LEFT JOIN omega_publication.materialization_evidence e
                ON e.materialization_run_id=h.materialization_run_id
             WHERE h.tenant_id=%s AND h.workspace_id=%s
               AND r.status IN ('published','legacy_unverified')
               AND r.object_uri IS NOT NULL
            """,
            (tenant, workspace),
        )
        return {
            (str(row[0]), str(row[1])): {
                "run_id": str(row[2]),
                "generation": int(row[3]),
                "object_uri": row[4],
                "row_count": int(row[5] or 0),
                "catalog": row[6] or [],
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
    clean = str(key or "").lstrip("/")
    if not materialized_object(clean):
        return True
    return clean in published_objects(context, bucket)


def published_prefix(key: str, context: dict[str, Any] | None, bucket: str) -> bool:
    clean = str(key or "").lstrip("/")
    if not materialized_object(clean):
        return True
    return any(
        value == clean.rstrip("/") or value.startswith(clean)
        for value in published_objects(context, bucket)
    )
