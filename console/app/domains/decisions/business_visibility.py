from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.decisions.provenance import strip_control_room_provenance
from app.services.control_room.business_projection import (
    filter_business_decisions,
    lineage_parent_ids,
)
from app.services.control_room.business_repository import fetch_lineage_rows


MAX_VISIBLE_DECISIONS = 500
DECISION_BATCH_SIZE = 500
CONTROL_ROOM_ACTION = "Decision creada desde Sala de Control"


def preserve_control_room_provenance(existing: Any, requested: Any) -> list[Any]:
    del existing
    # The PATCH helper has no server-side item/decision relation. Reserved
    # provenance is therefore removed; the control_room_items link remains truth.
    return strip_control_room_provenance(requested)


async def _linked_rows(
    conn: Any,
    *,
    workspace_id: str,
    decision_ids: Sequence[int],
    tenant_id: str | None = None,
    owner_id: int | None = None,
) -> tuple[list[Any], list[Any], set[int]]:
    if not decision_ids:
        return [], [], set()
    params: list[Any] = [workspace_id, list(decision_ids)]
    tenant_clause = ""
    if tenant_id:
        params.append(tenant_id)
        tenant_clause = f"AND tenant_id::text = ${len(params)}"
    owner_clause = ""
    if owner_id is not None:
        params.append(owner_id)
        owner_clause = f"AND owner_user_id = ${len(params)}"
    linked = await conn.fetch(
        f"""
        SELECT decision_id, item_id, item_kind, source_dataset, metadata
          FROM control_room_items
         WHERE workspace_id = $1
           AND decision_id = ANY($2::bigint[])
           {tenant_clause}
           {owner_clause}
        """,
        *params,
    )
    lineage = await fetch_lineage_rows(
        conn,
        workspace_id=workspace_id,
        parent_ids=lineage_parent_ids(linked),
        tenant_id=tenant_id,
        owner_id=owner_id,
    )
    origins = await conn.fetch(
        """
        SELECT DISTINCT action.decision_id
          FROM decision_actions action
          JOIN decisions decision ON decision.id = action.decision_id
         WHERE decision.workspace_id = $1
           AND action.decision_id = ANY($2::bigint[])
           AND action.action_text = $3
        """,
        workspace_id,
        list(decision_ids),
        CONTROL_ROOM_ACTION,
    )
    return list(linked), lineage, {int(row["decision_id"]) for row in origins}


async def filter_decision_rows(
    conn: Any,
    *,
    workspace_id: str,
    rows: Sequence[Mapping[str, Any]],
    tenant_id: str | None = None,
    owner_id: int | None = None,
) -> list[dict[str, Any]]:
    decisions = [dict(row) for row in rows]
    decision_ids = [int(row["id"]) for row in decisions if row.get("id") is not None]
    linked, lineage, control_room_origins = await _linked_rows(
        conn,
        workspace_id=workspace_id,
        decision_ids=decision_ids,
        tenant_id=tenant_id,
        owner_id=owner_id,
    )
    candidates = [
        {
            **row,
            "_control_room_origin": int(row["id"]) in control_room_origins,
        }
        for row in decisions
    ]
    visible = filter_business_decisions(
        candidates,
        linked,
        lineage_items=lineage,
    )
    for row in visible:
        row.pop("_control_room_origin", None)
    return visible


async def fetch_business_decisions(
    conn: Any,
    *,
    sql: str,
    params: Sequence[Any],
    workspace_id: str,
    tenant_id: str | None = None,
    owner_id: int | None = None,
    limit: int = MAX_VISIBLE_DECISIONS,
) -> list[dict[str, Any]]:
    target = max(1, min(int(limit), MAX_VISIBLE_DECISIONS))
    visible: list[dict[str, Any]] = []
    cursor: tuple[Any, int] | None = None
    while len(visible) < target:
        page_limit = DECISION_BATCH_SIZE
        if cursor is None:
            limit_param = len(params) + 1
            page_sql = f"{sql} LIMIT ${limit_param}"
            page_params = [*params, page_limit]
        else:
            created_param = len(params) + 1
            id_param = created_param + 1
            limit_param = id_param + 1
            page_sql = (
                f"SELECT * FROM ({sql}) AS decision_page "
                f"WHERE (decision_page.created_at, decision_page.id) "
                f"< (${created_param}, ${id_param}) "
                "ORDER BY decision_page.created_at DESC, decision_page.id DESC "
                f"LIMIT ${limit_param}"
            )
            page_params = [*params, cursor[0], cursor[1], page_limit]
        rows = await conn.fetch(
            page_sql,
            *page_params,
        )
        batch = list(rows)
        if not batch:
            break
        visible.extend(
            await filter_decision_rows(
                conn,
                workspace_id=workspace_id,
                rows=batch,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        )
        last = batch[-1]
        if last.get("created_at") is None or last.get("id") is None:
            break
        cursor = (last["created_at"], int(last["id"]))
        if len(batch) < page_limit:
            break
    return visible[:target]


async def count_business_decisions(
    conn: Any,
    *,
    sql: str,
    params: Sequence[Any],
    workspace_id: str,
    tenant_id: str | None = None,
    owner_id: int | None = None,
) -> int:
    total = 0
    cursor: tuple[Any, int] | None = None
    while True:
        if cursor is None:
            page_sql = f"{sql} LIMIT ${len(params) + 1}"
            page_params = [*params, DECISION_BATCH_SIZE]
        else:
            created_param = len(params) + 1
            id_param = created_param + 1
            limit_param = id_param + 1
            page_sql = (
                f"SELECT * FROM ({sql}) AS decision_page "
                "WHERE (decision_page.created_at, decision_page.id) "
                f"< (${created_param}, ${id_param}) "
                "ORDER BY decision_page.created_at DESC, decision_page.id DESC "
                f"LIMIT ${limit_param}"
            )
            page_params = [*params, cursor[0], cursor[1], DECISION_BATCH_SIZE]
        batch = list(await conn.fetch(page_sql, *page_params))
        if not batch:
            break
        total += len(
            await filter_decision_rows(
                conn,
                workspace_id=workspace_id,
                rows=batch,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        )
        last = batch[-1]
        if last.get("created_at") is None or last.get("id") is None:
            break
        cursor = (last["created_at"], int(last["id"]))
        if len(batch) < DECISION_BATCH_SIZE:
            break
    return total
