from __future__ import annotations

import hashlib
import asyncio
import os
from urllib.parse import urlsplit

import asyncpg
from app.services.intelligence.utils import workspace_scope


def _gold_dsn() -> str:
    return (
        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    ).replace("postgresql+psycopg2://", "postgresql://")


def _object_ref(uri: str) -> tuple[str, str]:
    parsed = urlsplit(str(uri or ""))
    if parsed.scheme not in {"s3", "gs"} or not parsed.netloc:
        return "", ""
    return parsed.netloc, parsed.path.lstrip("/")


def materialized_object_key(key: str) -> bool:
    return str(key or "").lstrip("/").split("/", 1)[0] in {"silver", "gold"}


async def published_object_references(user: dict | None, bucket: str) -> dict[str, str]:
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id or not workspace_id or not _gold_dsn():
        return {}
    conn = await asyncpg.connect(_gold_dsn(), timeout=5, command_timeout=10)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true), set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            rows = await conn.fetch(
                """
                SELECT r.object_uri,r.object_checksum
                  FROM omega_publication.dataset_publication_heads h
                  JOIN omega_publication.materialization_runs r
                    ON r.materialization_run_id=h.materialization_run_id
                 WHERE h.tenant_id=$1 AND h.workspace_id=$2
                   AND r.status='published'
                   AND r.object_uri IS NOT NULL
                   AND r.object_checksum ~ '^[0-9a-f]{64}$'
                """,
                tenant_id,
                workspace_id,
            )
            refs = {
                _object_ref(row["object_uri"]): str(row["object_checksum"])
                for row in rows
            }
            return {
                key: checksum
                for (object_bucket, key), checksum in refs.items()
                if object_bucket == bucket and key
            }
    finally:
        await conn.close()


async def published_object_keys(user: dict | None, bucket: str) -> set[str]:
    return set(await published_object_references(user, bucket))


async def publication_epoch(user: dict | None) -> str | None:
    """Return an opaque workspace head-set identity for public read caches."""
    tenant_id, workspace_id = workspace_scope(user)
    dsn = _gold_dsn()
    if not tenant_id or not workspace_id or not dsn:
        return None
    conn = None
    try:
        conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true), set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            rows = await conn.fetch(
                """SELECT dataset,layer,materialization_run_id::text,generation
                     FROM omega_publication.dataset_publication_heads
                    WHERE tenant_id=$1 AND workspace_id=$2
                    ORDER BY dataset,layer""",
                tenant_id,
                workspace_id,
            )
        payload = "\0".join("|".join(str(row[key]) for key in range(4)) for row in rows)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except (asyncpg.PostgresError, OSError, TimeoutError):
        return None
    finally:
        if conn is not None:
            await conn.close()


async def published_object(key: str, user: dict | None, bucket: str) -> bool:
    clean = str(key or "").lstrip("/")
    if not materialized_object_key(clean):
        return True
    return clean in await published_object_keys(user, bucket)


def _read_object_bytes(s3_client, bucket: str, key: str) -> bytes:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        return body.read()
    finally:
        close = getattr(body, "close", None)
        if close:
            close()


async def verified_published_object(
    s3_client, key: str, user: dict | None, bucket: str
) -> bytes | None:
    clean = str(key or "").lstrip("/")
    if not materialized_object_key(clean):
        return None
    expected = (await published_object_references(user, bucket)).get(clean)
    if not expected:
        return None
    try:
        raw = await asyncio.to_thread(_read_object_bytes, s3_client, bucket, clean)
    except Exception:
        return None
    return raw if hashlib.sha256(raw).hexdigest() == expected else None


def visible_materialized_prefix(prefix: str, published: set[str]) -> bool:
    clean = str(prefix or "").lstrip("/")
    if not materialized_object_key(clean):
        return True
    return any(key == clean.rstrip("/") or key.startswith(clean) for key in published)


def visible_materialized_object(key: str, published: set[str]) -> bool:
    clean = str(key or "").lstrip("/")
    return not materialized_object_key(clean) or clean in published
