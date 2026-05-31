"""Sprint v1.45 — proactive briefing v2 (Nivel 2).

The v1.44.2 ``proactive_service.briefing_for_user`` returns a list of
``Highlight`` dicts sorted by severity. v2 wraps that pipeline and
adds the two pieces the advanced copilot flow needs:

  * ``priority_score`` — numeric ranking (0..100) blending severity,
    age and an optional impact estimate, so the dashboard can render
    the cards in a meaningful order even when several share the same
    severity bucket.
  * ``next_action`` — a small structured suggestion the user can act
    on with one click (open a goal, run a watchdog, jump to a route).

v2 is strictly additive: it returns a superset shape so the existing
``/api/copilot/briefing`` endpoint keeps working unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services import proactive_service, watchdog_registry


logger = logging.getLogger(__name__)


_SEVERITY_BASE = {"critical": 80, "warning": 50, "info": 20}


def _priority_score(highlight: dict[str, Any]) -> int:
    base = _SEVERITY_BASE.get(highlight.get("severity") or "info", 20)
    # Failed extractions add weight, ditto pending actions: heuristic
    # category -> bump.
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
    """Map a highlight to a structured action suggestion.

    Returns ``None`` when there isn't a sensible next-step the UI can
    surface — we'd rather render nothing than a misleading button.
    """
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
    """Best-effort: surface 1-2 watchdogs matching this highlight's
    cartridge + category so the user can launch the right specialist
    diagnosis in one click.
    """
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
    user_id: int, *, limit: int = 6,
) -> list[dict[str, Any]]:
    """Enriched briefing — same source data as v1 plus
    ``priority_score``, ``next_action`` and matched ``watchdogs``.

    Re-sorts by priority_score descending (severity was the v1 sort key
    but is preserved as a tiebreaker via the underlying analyzer order).

    Audit round 3 hardening: the upstream
    ``proactive_service.briefing_for_user`` runs 4 SQL analyzers and
    can raise on schema drift / pool exhaustion. The previous code let
    the exception propagate, turning a transient blip into a 500 on
    the dashboard's first paint. The mitigation here is to log and
    return an empty briefing — the UI already renders the empty state
    cleanly, which is strictly better UX than a red banner.
    """
    try:
        highlights = await proactive_service.briefing_for_user(
            user_id, limit=limit * 2,
        )
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
