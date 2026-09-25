from __future__ import annotations

import asyncio
import hashlib
import os
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

import asyncpg

from app.services import auth
from app.services.db_scope import scoped_db
from app.services.intelligence.persistence import persist_artifacts_on_connection
from app.services.intelligence.publication_trace import exact_bindings
from app.services.intelligence.utils import json_dumps, workspace_scope
from omega_lakehouse import storage_from_env


def _dsn(name: str) -> str:
    return str(os.environ.get(name) or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


async def assert_expected_pipeline_run(
    user: dict[str, Any], *, expected_run_id: str, cartridge_id: str
) -> None:
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id or not expected_run_id:
        raise RuntimeError("expected pipeline authority is incomplete")
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        allowed = await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM pipeline_runs
                 WHERE tenant_id=$1 AND workspace_id=$2 AND run_id=$3
                   AND cartridge_id=$4 AND status IN ('running','success'))""",
            tenant_id,
            workspace_id,
            expected_run_id,
            cartridge_id,
        )
    if allowed is not True:
        raise RuntimeError("expected pipeline run is unavailable")


def _validate_object(binding: dict[str, Any]) -> None:
    storage = storage_from_env()
    uri = str(binding["object_uri"])
    parsed = urlsplit(uri)
    key = parsed.path.lstrip("/")
    if not key or storage.uri_for(key) != uri:
        raise RuntimeError("Gold publication object scope mismatch")
    digest = hashlib.sha256()
    for chunk in storage.iter_chunks(
        key, expected_version=str(binding["object_version"])
    ):
        digest.update(chunk)
    if digest.hexdigest() != binding["object_checksum"]:
        raise RuntimeError("Gold publication object mismatch")


async def _validate_exact_gold(
    tenant_id: str, workspace_id: str, binding: dict[str, Any]
) -> None:
    dsn = _dsn("GOLD_DATABASE_URL")
    if not dsn:
        raise RuntimeError("Gold publication authority is unavailable")
    conn = await asyncpg.connect(dsn, command_timeout=10)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            exact = await conn.fetchval(
                """SELECT EXISTS(
                  SELECT 1 FROM omega_publication.materialization_runs r
                  JOIN omega_publication.materialization_receipts rec
                    ON rec.materialization_run_id=r.materialization_run_id
                  JOIN omega_publication.materialization_evidence e
                    ON e.materialization_run_id=r.materialization_run_id
                   AND e.tenant_id=r.tenant_id AND e.workspace_id=r.workspace_id
                   AND e.dataset=r.dataset AND e.layer=r.layer
                 WHERE r.tenant_id=$1 AND r.workspace_id=$2 AND r.dataset=$3
                   AND r.layer='gold' AND r.status='published'
                   AND r.materialization_run_id=$4::uuid
                   AND rec.receipt_id=$5::uuid AND rec.generation=$6
                   AND r.object_uri=$7 AND r.object_version=$8
                   AND r.object_checksum=$9 AND r.schema_digest=$10
                   AND r.evidence_digest=$11
                   AND e.object_uri=r.object_uri AND e.object_version=r.object_version
                   AND e.object_checksum=r.object_checksum
                   AND e.schema_digest=r.schema_digest
                   AND e.evidence_digest=r.evidence_digest)""",
                tenant_id,
                workspace_id,
                binding["dataset"],
                binding["materialization_run_id"],
                binding["receipt_id"],
                binding["head_generation"],
                binding["object_uri"],
                binding["object_version"],
                binding["object_checksum"],
                binding["schema_digest"],
                binding["evidence_digest"],
            )
    finally:
        await conn.close()
    if exact is not True:
        raise RuntimeError("Gold publication binding mismatch")
    await asyncio.to_thread(_validate_object, binding)


async def stage_gold_refresh_authority(
    user: dict[str, Any],
    *,
    expected_run_id: str,
    expected_run_ref: str,
    intelligence_run_id: int,
    publication_trace: dict[str, dict[str, Any]],
    expected_datasets: list[str],
) -> None:
    tenant_id, workspace_id = workspace_scope(user)
    bindings = exact_bindings(publication_trace, expected_datasets)
    for binding in bindings:
        await _validate_exact_gold(tenant_id, workspace_id, binding)
    dsn = _dsn("OUTCOME_BINDER_DATABASE_URL")
    if not dsn:
        raise RuntimeError("operational outcome binder is unavailable")
    conn = await asyncpg.connect(dsn, command_timeout=10)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                "set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            for item in bindings:
                await conn.fetchval(
                    "SELECT stage_operational_outcome_binding("
                    "$1,$2,$3,$4,$5::uuid,$6::uuid,$7,$8,$9,$10,$11)",
                    expected_run_id,
                    expected_run_ref,
                    intelligence_run_id,
                    item["dataset"],
                    item["materialization_run_id"],
                    item["receipt_id"],
                    item["head_generation"],
                    item["object_version"],
                    item["object_checksum"],
                    item["schema_digest"],
                    item["evidence_digest"],
                )
    finally:
        await conn.close()


async def persist_gold_refresh_binding(
    user: dict[str, Any],
    *,
    expected_run_id: str,
    expected_run_ref: str,
    intelligence_result: dict[str, Any],
    publication_trace: dict[str, Any],
    expected_datasets: list[str],
) -> str:
    del publication_trace, expected_datasets
    tenant_id, workspace_id = workspace_scope(user)
    try:
        intelligence_run_id = int(intelligence_result["intelligence_run_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Intelligence outcome identity is incomplete") from exc
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        value = await conn.fetchval(
            "SELECT finalize_operational_outcome_binding($1,$2,$3)",
            expected_run_id,
            expected_run_ref,
            intelligence_run_id,
        )
    digest = str(value or "")
    if len(digest) != 64:
        raise RuntimeError("operational outcome binding was not persisted")
    return digest


async def resume_failed_gold_refresh(
    user: dict[str, Any], *, run_id: int, run_ref: str, request: dict[str, Any]
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE intelligence_runs
               SET status='running', errors='[]'::jsonb, completed_at=NULL,
                   updated_at=NOW(),
                   metadata=metadata || '{"gold_refresh_retry":true}'::jsonb
             WHERE workspace_id=$1 AND id=$2 AND run_ref=$3
               AND run_mode='gold_refresh' AND status='failed'
               AND request=$4::jsonb
             RETURNING *
            """,
            workspace_id,
            run_id,
            run_ref,
            json_dumps(request),
        )
        if row is None:
            raise RuntimeError("failed gold refresh cannot be resumed")
        return dict(row)


async def persist_gold_refresh_pending(
    user: dict[str, Any],
    *,
    run_id: int,
    run_ref: str,
    artifacts: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    datasets_evaluated: list[dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    counts = Counter(str(item.get("status") or "unknown") for item in skipped)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        await persist_artifacts_on_connection(
            conn,
            tenant_id,
            workspace_id,
            user,
            artifacts,
            intelligence_run_id=run_id,
            run_ref=run_ref,
        )
        row = await conn.fetchrow(
            """
            UPDATE intelligence_runs
               SET status='binding_pending', datasets_evaluated=$4::jsonb,
                   signals_generated=$5, signals_skipped=$6,
                   dataset_unavailable_count=$7,
                   insufficient_history_count=$8, errors='[]'::jsonb,
                   metadata=metadata || $9::jsonb,
                   completed_at=NOW(), updated_at=NOW()
             WHERE workspace_id=$1 AND id=$2 AND run_ref=$3
               AND run_mode='gold_refresh' AND status='running'
             RETURNING *
            """,
            workspace_id,
            run_id,
            run_ref,
            json_dumps(datasets_evaluated),
            len(artifacts),
            len(skipped),
            counts.get("dataset_unavailable", 0),
            counts.get("insufficient_history", 0),
            json_dumps({"duration_ms": duration_ms, "skipped_counts": dict(counts)}),
        )
        if row is None:
            raise RuntimeError("gold refresh binding transition is unavailable")
        return dict(row)


__all__ = [
    "assert_expected_pipeline_run",
    "persist_gold_refresh_binding",
    "persist_gold_refresh_pending",
    "resume_failed_gold_refresh",
    "stage_gold_refresh_authority",
]
