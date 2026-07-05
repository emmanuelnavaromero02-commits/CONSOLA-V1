from __future__ import annotations

from typing import Any


async def ensure_sync_packaged_datasets(
    *,
    cartridge: str,
    user: dict[str, Any] | None,
    workspace_scope_from_user: Any,
    get_db_pool: Any,
    seed_packaged_datasets_for_workspace: Any,
    logger_info: Any,
) -> dict[str, Any]:
    if cartridge != "sap_successfactors":
        return {"status": "skipped", "reason": "cartridge_not_packaged"}
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await get_db_pool()
    result = await seed_packaged_datasets_for_workspace(
        pool,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        cartridge_id=cartridge,
    )
    logger_info(
        "sync packaged dataset seed cartridge=%s tenant=%s workspace=%s status=%s seeded_rows=%s",
        cartridge,
        tenant_id,
        workspace_id,
        result.get("status"),
        result.get("seeded_rows"),
    )
    return result
