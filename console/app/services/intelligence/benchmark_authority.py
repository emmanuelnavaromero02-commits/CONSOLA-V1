from __future__ import annotations

import os
from typing import Any

import asyncpg


BENCHMARK_DATASET = "sap_successfactors_talent_benchmark_internal"
BENCHMARK_AUTHORITY_DATASETS = frozenset(
    {
        BENCHMARK_DATASET,
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
        "sap_successfactors_talent_9box_operational",
        "sap_successfactors_talent_operational_features",
        "sap_successfactors_talent_simulation_inputs",
    }
)


def _console_dsn() -> str:
    return (os.environ.get("DATABASE_URL") or "").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


async def resolve_benchmark_approval_authority(
    tenant_id: str,
    workspace_id: str,
    materialization_head: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    dsn = _console_dsn()
    if not dsn or not tenant_id or not workspace_id or not materialization_head:
        return {}
    conn = None
    try:
        conn = await asyncpg.connect(dsn, command_timeout=5)
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true),"
                " set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            row = await conn.fetchrow(
                """
                SELECT id, tenant_id::text AS tenant_id,
                       workspace_id::text AS workspace_id, dataset,
                       materialization_head::text AS materialization_head,
                       actor_user_id, evidence_pack_id,
                       authorization_role_id, evidence_digest,
                       evidence_digest_version, evidence_item_count,
                       authorization_digest,
                       authorization_ref::text AS authorization_ref,
                       approval_status, approved_at, recorded_by_server
                  FROM talent_benchmark_approvals
                 WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
                   AND dataset=$3 AND materialization_head=$4::uuid
                   AND approval_status='approved' AND recorded_by_server=TRUE
                """,
                tenant_id,
                workspace_id,
                BENCHMARK_DATASET,
                materialization_head,
            )
        if not row:
            return {}
        entry = dict(row)
        entry["evidence_ref"] = f"evidence:{entry.pop('evidence_pack_id')}"
        approved_at = entry.get("approved_at")
        if hasattr(approved_at, "isoformat"):
            entry["approved_at"] = approved_at.isoformat()
        return {(tenant_id, workspace_id): entry}
    except Exception:
        return {}
    finally:
        if conn is not None:
            await conn.close()


def authority_revision(authority: dict[tuple[str, str], dict[str, Any]]) -> str:
    if not authority:
        return "unapproved"
    entry = next(iter(authority.values()))
    return ":".join(
        str(entry.get(field) or "")
        for field in ("id", "materialization_head", "authorization_ref")
    )
