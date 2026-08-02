from __future__ import annotations

import json
from typing import Any

from app.services import auth
from app.services.db_scope import scoped_db
from app.services.intelligence.publication_trace import exact_bindings
from app.services.intelligence.utils import workspace_scope


async def assert_expected_pipeline_run(
    user: dict[str, Any], *, expected_run_id: str, cartridge_id: str
) -> None:
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id or not expected_run_id:
        raise RuntimeError("expected pipeline authority is incomplete")
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        allowed = await conn.fetchval(
            """SELECT EXISTS(
                   SELECT 1 FROM pipeline_runs
                    WHERE tenant_id=$1 AND workspace_id=$2
                      AND run_id=$3 AND cartridge_id=$4
                      AND status IN ('running','success')
               )""",
            tenant_id,
            workspace_id,
            expected_run_id,
            cartridge_id,
        )
    if allowed is not True:
        raise RuntimeError("expected pipeline run is unavailable")


async def persist_gold_refresh_binding(
    user: dict[str, Any],
    *,
    expected_run_id: str,
    expected_run_ref: str,
    intelligence_result: dict[str, Any],
    publication_trace: dict[str, dict[str, Any]],
    expected_datasets: list[str],
) -> str:
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id:
        raise RuntimeError("operational outcome scope is incomplete")
    run_id = intelligence_result.get("intelligence_run_id")
    if isinstance(run_id, bool):
        raise RuntimeError("Intelligence outcome identity is incomplete")
    try:
        intelligence_run_id = int(run_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Intelligence outcome identity is incomplete") from exc
    if intelligence_run_id < 1:
        raise RuntimeError("Intelligence outcome identity is incomplete")
    if str(intelligence_result.get("run_ref") or "") != expected_run_ref:
        raise RuntimeError("Intelligence outcome reference mismatch")
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        existing = await conn.fetchrow(
            """SELECT min(outcome_digest) AS outcome_digest,
                      array_agg(dataset ORDER BY dataset) AS datasets,
                      bool_and(intelligence_run_id=$4 AND run_ref=$2) AS exact
                 FROM operational_outcome_bindings
                WHERE tenant_id=$1 AND workspace_id=$3 AND expected_run_id=$5""",
            tenant_id,
            expected_run_ref,
            workspace_id,
            intelligence_run_id,
            expected_run_id,
        )
        if existing and existing["outcome_digest"]:
            if not existing["exact"] or list(existing["datasets"] or []) != sorted(
                set(expected_datasets)
            ):
                raise RuntimeError("operational outcome replay mismatch")
            return str(existing["outcome_digest"])
        bindings = exact_bindings(publication_trace, expected_datasets)
        value = await conn.fetchval(
            "SELECT record_operational_outcome_binding($1,$2,$3,$4::jsonb)",
            expected_run_id,
            expected_run_ref,
            intelligence_run_id,
            json.dumps(bindings, sort_keys=True, separators=(",", ":")),
        )
    digest = str(value or "")
    if len(digest) != 64:
        raise RuntimeError("operational outcome binding was not persisted")
    return digest
