from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.intelligence.domain_aggregate_support import (
    STATUS_DEGRADED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    as_int,
)
from app.services.intelligence.sap_b1_aggregates import APRENDIZAJE_NOTE, B1Result

WINDOW_DAYS = 90
MIN_ALERTS = 5
FALSE_POSITIVE_SHARE = 0.5
THRESHOLDS_BY_SOURCE = {
    "WB-B1-MARGEN": ("margin_min_pct",),
    "WB-B1-CADUCIDAD": ("expiry_red_days", "expiry_yellow_days", "expiry_horizon_days"),
    "WB-B1-ABASTO": ("coverage_red_days", "coverage_yellow_days", "safety_days"),
    "WB-B1-SEMAFORO": (),
}
LEARNING_SQL = """
    SELECT i.metadata->>'wisdom_bit_id' AS source,
           COUNT(*)::bigint AS alerts,
           COUNT(*) FILTER (WHERE i.decision_id IS NOT NULL)::bigint AS decisions,
           COUNT(*) FILTER (WHERE i.metadata->'alert_state'->>'state' = 'false_positive')::bigint AS false_positives,
           COUNT(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM prediction_outcomes po
                WHERE po.workspace_id = i.workspace_id AND po.signal_id = i.item_id))::bigint AS outcomes,
           COUNT(*) FILTER (WHERE d.outcome = 'achieved')::bigint AS achieved,
           COUNT(*) FILTER (WHERE d.outcome = 'not_achieved')::bigint AS not_achieved
      FROM control_room_items i
      LEFT JOIN decisions d ON d.id = i.decision_id
     WHERE i.workspace_id = $1::uuid
       AND i.cartridge_id = 'sap_b1'
       AND i.item_kind = 'agent_alert'
       AND i.metadata->>'wisdom_bit_id' LIKE 'WB-B1-%'
       AND i.first_seen_at >= NOW() - make_interval(days => $2)
     GROUP BY 1
     ORDER BY 1
"""


@dataclass
class Learning(B1Result):
    window_days: int = WINDOW_DAYS
    sources: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[dict[str, Any]] = field(default_factory=list)


def learning_from_rows(rows: list[Any]) -> Learning:
    sources, suggestions = [], []
    for row in rows:
        source = str(row["source"])
        alerts = as_int(row["alerts"]) or 0
        false_positives = as_int(row["false_positives"]) or 0
        achieved = as_int(row["achieved"]) or 0
        not_achieved = as_int(row["not_achieved"]) or 0
        item = {"source": source, "alerts": alerts, "decisions": as_int(row["decisions"]) or 0,
                "outcomes": as_int(row["outcomes"]) or 0, "false_positives": false_positives,
                "achieved": achieved, "not_achieved": not_achieved}
        sources.append(item)
        thresholds = THRESHOLDS_BY_SOURCE.get(source, ())
        if alerts >= MIN_ALERTS and false_positives / alerts >= FALSE_POSITIVE_SHARE and thresholds:
            suggestions.append({
                "source": source, "thresholds": list(thresholds),
                "reason": f"{false_positives} de {alerts} alertas de {source} se marcaron como falso positivo en "
                          f"{WINDOW_DAYS} días: conviene revisar el umbral.",
            })
        if not_achieved and not_achieved > achieved and thresholds:
            suggestions.append({
                "source": source, "thresholds": list(thresholds),
                "reason": f"{not_achieved} de {achieved + not_achieved} decisiones sobre {source} no alcanzaron su "
                          "objetivo: conviene revisar el umbral o la acción recomendada.",
            })
    decisions = sum(item["decisions"] for item in sources)
    return Learning(
        status=STATUS_READY if sources else STATUS_DEGRADED,
        proxy_note=APRENDIZAJE_NOTE,
        notes=[] if sources else ["todavía no hay alertas de SAP Business One en la ventana"],
        breaches=[item["reason"] for item in suggestions],
        sources=sources,
        suggestions=suggestions,
        period=f"últimos {WINDOW_DAYS} días" if decisions or sources else None,
    )


async def query_aprendizaje(user: dict | None) -> Learning:
    from app.services import auth
    from app.services.db_scope import scoped_db_for_user

    try:
        pool = await auth.pool()
        async with scoped_db_for_user(pool, user) as (conn, _tenant_id, workspace_id):
            rows = await conn.fetch(LEARNING_SQL, workspace_id, WINDOW_DAYS)
    except Exception as exc:  # noqa: BLE001
        return Learning(status=STATUS_UNAVAILABLE, proxy_note=APRENDIZAJE_NOTE, error=f"unavailable: {type(exc).__name__}")
    return learning_from_rows(list(rows))


__all__ = ["Learning", "learning_from_rows", "query_aprendizaje"]
