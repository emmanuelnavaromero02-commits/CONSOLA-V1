from __future__ import annotations

import logging
from typing import Any

from app.services import proactive_service, watchdog_registry


logger = logging.getLogger(__name__)


_SEVERITY_BASE = {"critical": 80, "warning": 50, "info": 20}


def _priority_score(highlight: dict[str, Any]) -> int:
    base = _SEVERITY_BASE.get(highlight.get("severity") or "info", 20)
    cat = (highlight.get("category") or "").lower()
    bump = {
        "failure": 15,
        "failures": 15,
        "pending": 10,
        "volume": 5,
        "freshness": 5,
    }.get(cat, 0)
    return max(0, min(100, base + bump))


def _suggest_next_action(highlight: dict[str, Any]) -> dict[str, Any] | None:
    cat = (highlight.get("category") or "").lower()
    cart = highlight.get("cartridge")
    if cat == "freshness" and cart:
        return {
            "kind": "run_extraction",
            "label": f"Forzar extracción {cart}",
            "cartridge": cart,
            "href": f"/cartridges/{cart}",
        }
    if cat in ("failure", "failures") and cart:
        return {
            "kind": "open_pipeline",
            "label": f"Revisar runs fallidos de {cart}",
            "cartridge": cart,
            "href": f"/viewer?cartridge={cart}",
        }
    if cat == "pending":
        return {
            "kind": "open_control_room",
            "label": "Ir al Control Room",
            "href": "/control-room",
        }
    if cat == "volume" and cart:
        return {
            "kind": "open_dataset",
            "label": f"Investigar volumen {cart}",
            "cartridge": cart,
            "href": f"/explorer?cartridge={cart}",
        }
    return None


async def _attach_watchdogs(
    highlight: dict[str, Any],
) -> list[dict[str, Any]]:
    intent = f"{highlight.get('title','')} {highlight.get('body','')} {highlight.get('category','')}"
    try:
        matches = await watchdog_registry.relevant_watchdogs(
            intent,
            cartridge_id=highlight.get("cartridge"),
            min_score=0.1, limit=2,
        )
    except Exception:
        logger.debug("watchdog match failed", exc_info=True)
        return []
    return [
        {
            "cartridge_id": wd.get("cartridge_id"),
            "slug": wd.get("slug"),
            "name": wd.get("name"),
        }
        for wd in matches
    ]


async def briefing_v2_for_user(
    user_id: int, *, limit: int = 6, user_context: dict | None = None,
) -> list[dict[str, Any]]:
    try:
        kwargs: dict[str, Any] = {"limit": limit * 2}
        if user_context is not None:
            kwargs["user_context"] = user_context
        highlights = await proactive_service.briefing_for_user(user_id, **kwargs)
    except Exception:
        logger.warning(
            "briefing_v2_for_user: upstream proactive_service failed; "
            "returning empty briefing",
            exc_info=True,
        )
        return []

    enriched: list[dict[str, Any]] = []
    for h in highlights:
        item = dict(h)
        item["priority_score"] = _priority_score(h)
        item["next_action"] = _suggest_next_action(h)
        item["watchdogs"] = await _attach_watchdogs(h)
        enriched.append(item)
    enriched.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    return enriched[:limit]
