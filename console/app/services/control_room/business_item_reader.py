from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from app.services.control_room.business_projection import (
    eligible_item_ids,
    filter_business_items,
    lineage_parent_ids,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_lineage import item_kinds, parent_references
from app.services.control_room.business_access import (
    can_read_workspace_wide,
    owner_projection,
    owner_scope_id,
    workspace_scope,
)
from app.services.control_room.business_repository import fetch_lineage_rows


RowConverter = Callable[[Mapping[str, Any]], dict[str, Any]]
RowPredicate = Callable[[Mapping[str, Any]], bool]
PersistedLoader = Callable[[str], Awaitable[dict[str, Any] | None]]
ItemCollector = Callable[[], Awaitable[Mapping[str, Any]]]
LineageLoader = Callable[[Sequence[str]], Awaitable[Sequence[Mapping[str, Any]]]]

_PERSISTED_COMMAND_KINDS = frozenset(
    {"agent_alert", "intelligence_signal", "source_state"}
)


async def _page(
    conn: Any,
    *,
    workspace_id: str,
    tenant_id: str | None,
    owner_id: int | None,
    kinds: Sequence[str],
    cursor: tuple[int, datetime, str] | None,
    page_size: int,
) -> list[Any]:
    params: list[Any] = [workspace_id]
    clauses = ["workspace_id = $1"]
    if kinds:
        params.append(list(kinds))
        clauses.append(f"item_kind = ANY(${len(params)}::text[])")
    if tenant_id:
        params.append(tenant_id)
        clauses.append(f"tenant_id::text = ${len(params)}")
    if owner_id is not None:
        params.append(owner_id)
        clauses.append(f"owner_user_id = ${len(params)}")
    if cursor is not None:
        params.extend(cursor)
        priority_param = len(params) - 2
        seen_param = len(params) - 1
        id_param = len(params)
        clauses.append(
            "(COALESCE(priority_score, 0), last_seen_at, item_id) "
            f"< (${priority_param}, ${seen_param}, ${id_param})"
        )
    params.append(page_size)
    return list(
        await conn.fetch(
            f"""
            SELECT tenant_id, workspace_id, owner_user_id, item_id,
                   cartridge_id, domain, source_dataset, item_kind, title,
                   severity, status, decision_id, entity_kind, entity_id,
                   entity_label, anomaly_type, metadata, first_seen_at,
                   last_seen_at, resolved_at, dismissed_at, impact_estimate,
                   impact_currency, confidence, priority_score,
                   selected_option_id, execution_status
              FROM control_room_items
             WHERE {' AND '.join(clauses)}
             ORDER BY COALESCE(priority_score, 0) DESC, last_seen_at DESC,
                      item_id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    )


async def fetch_eligible_persisted_items(
    conn: Any,
    *,
    workspace_id: str,
    tenant_id: str | None,
    owner_id: int | None,
    kinds: Sequence[str],
    row_to_item: RowConverter,
    discard: RowPredicate | None = None,
    limit: int | None = 200,
    page_size: int = 200,
) -> list[dict[str, Any]]:
    target = None if limit is None else max(1, int(limit))
    size = max(1, min(int(page_size), 500))
    visible: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    cursor: tuple[int, datetime, str] | None = None
    while target is None or len(visible) < target:
        rows = await _page(
            conn,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            kinds=kinds,
            cursor=cursor,
            page_size=size,
        )
        if not rows:
            break
        candidates = [row for row in rows if discard is None or not discard(row)]
        seeds = [row_to_item(row) for row in candidates]
        parents = await fetch_lineage_rows(
            conn,
            workspace_id=workspace_id,
            parent_ids=lineage_parent_ids(candidates),
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        lineage = [row_to_item(row) for row in parents]
        seed_ids = {
            str(item.get("id") or item.get("item_id") or "").strip() for item in seeds
        }
        lineage = [
            item
            for item in lineage
            if str(item.get("id") or item.get("item_id") or "").strip() not in seed_ids
        ]
        eligible = eligible_item_ids(filter_business_items([*lineage, *seeds]))
        for item in seeds:
            item_id = str(item.get("id") or "")
            if item_id in eligible and item_id not in seen_ids:
                visible.append(item)
                seen_ids.add(item_id)
                if target is not None and len(visible) == target:
                    break
        last = rows[-1]
        last_seen_at = last.get("last_seen_at")
        if not isinstance(last_seen_at, datetime) or not last.get("item_id"):
            break
        cursor = (
            int(last.get("priority_score") or 0),
            last_seen_at,
            str(last["item_id"]),
        )
        if len(rows) < size:
            break
    return visible


async def resolve_business_item_lookup(
    item_id: str,
    *,
    load_persisted: PersistedLoader,
    collect_items: ItemCollector,
    load_lineage: LineageLoader,
    normalize_lineage: RowConverter,
) -> tuple[dict[str, Any] | None, set[str]]:
    """Resolve a command item and its eligible lineage without HTTP concerns."""
    eligible_parent_ids: set[str] = set()
    item = await load_persisted(item_id)
    if item is not None and not item_kinds(item) & _PERSISTED_COMMAND_KINDS:
        item = None

    persisted_item = item
    if persisted_item is not None and (refs := parent_references(persisted_item).ids):
        lineage_rows = await load_lineage(sorted(refs))
        eligible_parent_ids = eligible_item_ids(
            normalize_lineage(row) for row in lineage_rows
        )
    persisted_eligible = (
        classify_business_item(
            persisted_item,
            eligible_parent_ids=eligible_parent_ids,
        ).eligible
        if persisted_item is not None
        else False
    )
    if item is None or not persisted_eligible:
        collected = await collect_items()
        business_items = [
            dict(candidate)
            for candidate in collected.get("items", [])
            if isinstance(candidate, Mapping)
        ]
        diagnostics = [
            dict(candidate)
            for candidate in collected.get("diagnostics", [])
            if isinstance(candidate, Mapping)
        ]
        eligible_parent_ids = eligible_item_ids(business_items)
        live_item = next(
            (
                candidate
                for candidate in [*business_items, *diagnostics]
                if str(candidate.get("id") or "") == item_id
            ),
            None,
        )
        if (
            live_item is not None
            and classify_business_item(
                live_item,
                eligible_parent_ids=eligible_parent_ids,
            ).eligible
        ):
            item = {
                **live_item,
                **owner_projection(live_item, persisted_item or {}),
            }
        elif persisted_item is not None:
            item = persisted_item
        else:
            item = live_item
    return item, eligible_parent_ids


async def resolve_scoped_business_item_lookup(
    item_id: str,
    user: Mapping[str, Any],
    *,
    load_persisted: PersistedLoader,
    collect_items: ItemCollector,
    normalize_lineage: RowConverter,
    pool_factory: Callable[[], Awaitable[Any]],
    run_scoped: Callable[..., Awaitable[Any]],
) -> tuple[dict[str, Any] | None, set[str]]:
    tenant_id, workspace_id = workspace_scope(user)
    owner_id = owner_scope_id(user)
    if not can_read_workspace_wide(user) and owner_id is None:
        return None, set()

    async def _load_lineage(parent_ids: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        pool = await pool_factory()

        async def _load(
            conn: Any, _tenant_id: str | None, _workspace_id: str
        ) -> Sequence[Mapping[str, Any]]:
            return await fetch_lineage_rows(
                conn,
                workspace_id=workspace_id,
                parent_ids=parent_ids,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )

        return await run_scoped(pool, user, _load)

    return await resolve_business_item_lookup(
        item_id,
        load_persisted=load_persisted,
        collect_items=collect_items,
        load_lineage=_load_lineage,
        normalize_lineage=normalize_lineage,
    )


__all__ = (
    "fetch_eligible_persisted_items",
    "resolve_business_item_lookup",
    "resolve_scoped_business_item_lookup",
)
