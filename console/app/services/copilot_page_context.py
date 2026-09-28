from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.domains.copilot.context_payloads import (
    json_prompt_snapshot,
    looks_like_control_room_page,
    render_page_context,
    sanitise_page_context,
)
from app.services import control_room_service, copilot_context_service, permissions


logger = logging.getLogger(__name__)


async def control_room_live_context_for_prompt(
    page_context: dict[str, Any],
    user: dict[str, Any],
) -> str | None:
    if not looks_like_control_room_page(page_context):
        return None
    if not permissions.has_permission(user, "datasets.read"):
        return None
    has_operations_read = permissions.has_permission(user, "operations.read")

    snapshot: dict[str, Any] = {"available": True}

    async def _safe(name: str, loader) -> None:
        try:
            raw = await loader()
            projected = copilot_context_service.project_control_room_diagnostic(
                name, raw
            )
            snapshot[name] = projected or {"available": False}
        except HTTPException:
            snapshot[name] = {"available": False}
        except Exception:
            logger.debug("control room live context %s failed", name, exc_info=True)
            snapshot[name] = {"available": False}

    await _safe(
        "sap_successfactors_talent_kpis",
        lambda: control_room_service.sap_successfactors_talent_kpis(user),
    )
    await _safe(
        "sap_successfactors_talent_overview",
        lambda: control_room_service.sap_successfactors_talent_overview(user),
    )
    if has_operations_read:
        await _safe("ops_summary", lambda: control_room_service.ops_summary(user))
        await _safe(
            "sap_successfactors_talent_metadata_readiness",
            lambda: control_room_service.sap_successfactors_talent_metadata_readiness(
                user
            ),
        )
        await _safe(
            "agents_ops",
            lambda: control_room_service.agents_ops(user, limit=8),
        )

    return json_prompt_snapshot(snapshot)


async def page_context_prompt_block(
    raw_page_context: Any,
    user: dict[str, Any],
) -> str:
    """Sanitized <USER_PAGE_CONTEXT> block for the system prompt; "" when empty."""
    ctx = sanitise_page_context(raw_page_context)
    if not ctx:
        return ""
    try:
        live_context = await control_room_live_context_for_prompt(ctx, user)
    except Exception:
        logger.debug("control room live context enrichment failed", exc_info=True)
        live_context = None
    if live_context:
        ctx = {**ctx, "live_control_room_snapshot": live_context}
    return render_page_context(ctx)
