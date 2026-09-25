from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def validate_entity_name(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    entity = value.strip()
    if not _IDENT_RE.fullmatch(entity):
        raise HTTPException(400, "Invalid entity name: use letters, numbers and underscores only")
    return entity


def _dataset_matches_entity(ds: dict[str, Any], entity: str | None) -> bool:
    if not entity:
        return True
    if str(ds.get("name") or "").lower() == entity.lower():
        return True
    for source in ds.get("sources") or []:
        parts = str(source).strip("/").split("/")
        if parts and parts[-1].lower() == entity.lower():
            return True
    return False


async def preview_legacy_master(
    *,
    entity: str | None,
    cartridge: str | None,
    limit: int,
    user_context: dict[str, Any],
    list_datasets: Callable[[], Awaitable[list[dict[str, Any]]]],
    invoke_refinement: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    entity = validate_entity_name(entity)
    cartridge = validate_entity_name(cartridge) if cartridge else None
    datasets = await list_datasets()
    candidates = [
        ds for ds in datasets
        if (ds.get("layer") or "").lower() == "gold"
        and (not cartridge or not ds.get("cartridge") or ds.get("cartridge") == cartridge)
        and _dataset_matches_entity(ds, entity)
    ]
    if not candidates:
        target = entity or "any entity"
        scope = f" for cartridge {cartridge}" if cartridge else ""
        raise HTTPException(
            404,
            f"No Gold dataset registered for {target}{scope}. Build the Gold layer before preview.",
        )

    ds = candidates[0]
    result = await invoke_refinement(
        "query_dataset",
        {"name": ds["name"], "limit": limit, "user_context": user_context},
    )
    rows = result.get("data") or result.get("rows") or []
    rows = rows if isinstance(rows, list) else []
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else result.get("columns") or []
    return {
        "layer": "gold",
        "entity": entity,
        "dataset": ds["name"],
        "columns": columns,
        "rows": rows,
        "total": len(rows),
        "source": "refinement.query_dataset",
    }


async def preview_master(
    *,
    entity: str | None,
    cartridge: str | None,
    limit: int,
    user_context: dict[str, Any],
    list_datasets: Callable[[], Awaitable[list[dict[str, Any]]]],
    invoke_refinement: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    return await preview_legacy_master(
        entity=entity,
        cartridge=cartridge,
        limit=limit,
        user_context=user_context,
        list_datasets=list_datasets,
        invoke_refinement=invoke_refinement,
    )
