from __future__ import annotations

import logging
from typing import Any

from app.domains.apps.grants import (
    lock_workspace_reconciliation,
    reconcile_workspace,
)
from app.services.db_scope import SET_SCOPE_SQL


logger = logging.getLogger(__name__)


async def reconcile_packaged_app_grants(pool: Any) -> None:
    async with pool.acquire() as conn:
        workspaces = await conn.fetch(
            """SELECT w.tenant_id, w.id AS workspace_id
                 FROM public.workspaces w
                WHERE w.tenant_id IS NOT NULL
                ORDER BY w.tenant_id, w.id"""
        )
        for workspace in workspaces:
            tenant_id = str(workspace["tenant_id"])
            workspace_id = str(workspace["workspace_id"])
            async with conn.transaction():
                await lock_workspace_reconciliation(
                    conn,
                    workspace_id=workspace_id,
                )
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                installations = await conn.fetch(
                    """SELECT DISTINCT ci.cartridge_id
                         FROM public.cartridge_installations ci
                        WHERE ci.tenant_id = $1::uuid
                          AND ci.workspace_id = $2::uuid
                          AND ci.status = 'ready'
                        ORDER BY ci.cartridge_id""",
                    tenant_id,
                    workspace_id,
                )
                for installation in installations:
                    cartridge_id = str(installation["cartridge_id"])
                    summary = await reconcile_workspace(
                        conn,
                        cartridge_id=cartridge_id,
                    )
                    logger.info(
                        "[app-grants] startup reconcile workspace=%s "
                        "cartridge=%s granted=%s revoked=%s",
                        workspace_id,
                        cartridge_id,
                        summary.get("granted"),
                        summary.get("revoked"),
                    )
