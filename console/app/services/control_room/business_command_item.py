from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Set
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import (
    actor_id,
    can_read_workspace_wide,
    workspace_scope,
)
from app.services.control_room.business_eligibility import (
    BusinessEligibilityError,
    require_business_eligible,
)
from app.services.control_room.business_item_reader import (
    resolve_scoped_business_item_lookup,
)
from app.services.control_room.business_persisted_row import persisted_business_item
from app.services.control_room.business_workflow_provenance import (
    workflow_is_quarantined,
)


PoolFactory = Callable[[], Awaitable[Any]]
ScopedRunner = Callable[..., Awaitable[Any]]
ItemCollector = Callable[[], Awaitable[Mapping[str, Any]]]
ItemLoader = Callable[[str], Awaitable[dict[str, Any] | None]]
ItemProjector = Callable[..., dict[str, Any]]


async def load_persisted_command_item(
    item_id: str,
    user: Mapping[str, Any],
    *,
    pool_factory: PoolFactory,
    run_scoped: ScopedRunner,
    item_statuses: Set[str],
    severity_weights: Mapping[str, int],
) -> dict[str, Any] | None:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id, item_id]
    tenant_clause = ""
    if tenant_id:
        params.append(tenant_id)
        tenant_clause = f"AND tenant_id::text = ${len(params)}"
    owner_id = None
    if not can_read_workspace_wide(user):
        owner_id = actor_id(user.get("id"))
        if owner_id is None:
            return None
    pool = await pool_factory()

    async def _load(
        conn: Any, _tenant_id: str | None, _workspace_id: str
    ) -> Mapping[str, Any] | None:
        return await conn.fetchrow(
            f"""
            SELECT tenant_id, workspace_id, owner_user_id, item_id,
                   cartridge_id, domain, source_dataset, item_kind, title,
                   severity, status, decision_id, entity_kind, entity_id,
                   entity_label, anomaly_type, metadata, first_seen_at,
                   last_seen_at, resolved_at, dismissed_at, impact_estimate,
                   impact_currency, confidence, priority_score,
                   selected_option_id, execution_status
              FROM control_room_items
             WHERE workspace_id = $1
               AND item_id = $2
               {tenant_clause}
            """,
            *params,
        )

    row = await run_scoped(pool, user, _load)
    if not row:
        return None
    persisted_owner = actor_id(row.get("owner_user_id"))
    if owner_id is not None and persisted_owner not in {None, owner_id}:
        raise HTTPException(404, "control room item not found")
    return persisted_business_item(
        row,
        expected_item_id=item_id,
        item_statuses=item_statuses,
        severity_weights=severity_weights,
    )


async def resolve_command_item(
    item_id: str,
    user: Mapping[str, Any],
    *,
    load_persisted: ItemLoader,
    collect_items: ItemCollector,
    normalize_lineage: Callable[[Mapping[str, Any]], dict[str, Any]],
    pool_factory: PoolFactory,
    run_scoped: ScopedRunner,
    projector: ItemProjector,
) -> dict[str, Any]:
    item, eligible_parent_ids = await resolve_scoped_business_item_lookup(
        item_id,
        user,
        load_persisted=load_persisted,
        collect_items=collect_items,
        normalize_lineage=normalize_lineage,
        pool_factory=pool_factory,
        run_scoped=run_scoped,
    )
    if item is None:
        raise HTTPException(404, "control room item not found")
    if workflow_is_quarantined(item):
        raise HTTPException(
            409,
            detail={
                "code": "item_workflow_quarantined",
                "message": "Control Room workflow is quarantined.",
            },
        )
    try:
        require_business_eligible(item, eligible_parent_ids=eligible_parent_ids)
    except BusinessEligibilityError as exc:
        raise HTTPException(
            409,
            detail={
                "code": exc.code,
                "message": "Control Room item is diagnostic-only.",
                "reason": exc.result.reason.value,
            },
        ) from None
    return projector(item, eligible_parent_ids=eligible_parent_ids)


__all__ = ("load_persisted_command_item", "resolve_command_item")
