from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_state_rows import state_rows


PoolFactory = Callable[[], Awaitable[Any]]
ScopedRunner = Callable[..., Awaitable[Any]]
PersistedHook = Callable[[Any, int, int], Awaitable[None]]


async def _owner_map(
    conn: Any, workspace_id: str, item_ids: Sequence[str]
) -> dict[str, int]:
    if not item_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT item_id, owner_user_id
          FROM control_room_items
         WHERE workspace_id = $1
           AND item_id = ANY($2::text[])
           AND owner_user_id IS NOT NULL
        """,
        workspace_id,
        list(item_ids),
    )
    return {str(row["item_id"]): int(row["owner_user_id"]) for row in rows}


async def _foreign_item_ids(
    conn: Any, workspace_id: str, item_ids: Sequence[str], actor_id: int | None
) -> set[str]:
    if not item_ids or not callable(getattr(conn, "fetch", None)):
        return set()
    rows = await conn.fetch(
        """
        SELECT item_id
          FROM control_room_items
         WHERE workspace_id = $1
           AND item_id = ANY($2::text[])
           AND owner_user_id IS DISTINCT FROM $3::bigint
        """,
        workspace_id,
        list(item_ids),
        actor_id,
    )
    return {str(row["item_id"]) for row in rows}


async def persist_refresh_items(
    items: Sequence[Mapping[str, Any]],
    *,
    user: Mapping[str, Any],
    tenant_id: str | None,
    workspace_id: str,
    actor_id: int | None,
    workspace_wide: bool,
    pool_factory: PoolFactory,
    run_scoped: ScopedRunner,
    impact_builder: Callable[..., Mapping[str, Any]],
    metadata_builder: Callable[..., Mapping[str, Any]],
    diagnostic_builder: Callable[..., Mapping[str, Any]],
    on_persisted: PersistedHook | None = None,
) -> None:
    if not items:
        return
    pool = await pool_factory()

    async def _persist(conn: Any, _tenant_id: str | None, _workspace_id: str) -> None:
        owner_by_item: dict[str, int] = {}
        item_ids = [
            str(item.get("id") or item.get("item_id") or "").strip()
            for item in items
            if str(item.get("id") or item.get("item_id") or "").strip()
        ]
        scoped_items = items
        if workspace_wide:
            owner_by_item = await _owner_map(conn, workspace_id, item_ids)
        else:
            # Owner-scoped writers refresh their own rows and never touch others'.
            foreign = await _foreign_item_ids(conn, workspace_id, item_ids, actor_id)
            scoped_items = [
                item
                for item in items
                if str(item.get("id") or item.get("item_id") or "").strip()
                not in foreign
            ]
            if not scoped_items:
                if on_persisted is not None:
                    await on_persisted(conn, 0, 0)
                return

        rows = state_rows(
            scoped_items,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_user_id=actor_id,
            impact_builder=impact_builder,
            metadata_builder=metadata_builder,
            diagnostic_builder=diagnostic_builder,
            owner_by_item=owner_by_item,
        )
        lapsed = await persist_item_rows(
            conn,
            rows,
            owner_scope_id=None if workspace_wide else actor_id,
            workspace_wide=workspace_wide,
        )
        if on_persisted is not None:
            # Same transaction: the audit record commits or rolls back with the rows.
            await on_persisted(conn, len(rows), lapsed)

    await run_scoped(pool, dict(user), _persist)


__all__ = ("persist_refresh_items",)
