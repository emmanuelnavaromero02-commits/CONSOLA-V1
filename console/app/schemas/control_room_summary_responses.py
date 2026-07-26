from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from app.schemas.control_room_public_projection import PublicProjectionModel


_CAPABILITY_LABELS = {
    "sap_hcm": "Personal y nomina",
    "sap_successfactors": "Talento y organizacion",
    "sap_s4hana": "Finanzas y operaciones",
    "replicon": "Servicios profesionales",
    "platform": "Plataforma",
}
_DOMAIN_LABELS = {
    "recursos humanos": "Recursos Humanos",
    "nomina": "Nomina",
    "finanzas": "Finanzas",
    "presupuestos": "Presupuestos",
    "compras": "Compras",
    "ventas": "Ventas",
    "operacion": "Operacion",
}


class SummaryCounts(PublicProjectionModel):
    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    open: int = 0
    acknowledged: int = 0
    snoozed: int = 0
    assigned: int = 0
    false_positive: int = 0


class SummaryBreakdown(PublicProjectionModel):
    label: str | None = None
    count: int = 0


class SummaryFinancialRisk(PublicProjectionModel):
    label: str | None = None
    margin_pct: float | int | None = None
    wip_usd: float | int | None = None
    margin_usd: float | int | None = None


class SummaryFinancial(PublicProjectionModel):
    revenue_usd: float | int | None = None
    billed_usd: float | int | None = None
    wip_usd: float | int | None = None
    cost_usd: float | int | None = None
    margin_usd: float | int | None = None
    margin_pct: float | int | None = None
    sales_revenue: float | int | None = None
    backlog_value: float | int | None = None
    open_orders: int | None = None
    oldest_backlog_days: int | None = None
    purchase_spend: float | int | None = None
    risk_projects: list[SummaryFinancialRisk] = Field(default_factory=list)


class SummaryThresholds(PublicProjectionModel):
    active: int = 0
    total: int = 0


class SummaryLesson(PublicProjectionModel):
    rule: str | None = None
    confidence: float | int | None = None
    created_at: str | None = None


class SummaryLessonPattern(PublicProjectionModel):
    capability: str | None = None
    count: int = 0
    avg_confidence: float | int | None = None
    latest_rule: str | None = None
    last_seen_at: str | None = None


class SummaryLessons(PublicProjectionModel):
    total: int = 0
    recent: list[SummaryLesson] = Field(default_factory=list)
    by_capability: list[SummaryBreakdown] = Field(default_factory=list)
    top_patterns: list[SummaryLessonPattern] = Field(default_factory=list)


def _count_breakdown(
    value: object, labels: Mapping[str, str]
) -> list[dict[str, object]]:
    if not isinstance(value, Mapping):
        return []
    rows: list[dict[str, object]] = []
    for raw_key, raw_count in value.items():
        label = labels.get(str(raw_key).strip().casefold())
        if label is None or type(raw_count) is not int:
            continue
        rows.append({"label": label, "count": raw_count})
    return sorted(rows, key=lambda row: str(row["label"]))


def _business_sources(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    totals: dict[str, int] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        label = _CAPABILITY_LABELS.get(str(item.get("cartridge") or "").casefold())
        count = item.get("count")
        if label is not None and type(count) is int:
            totals[label] = totals.get(label, 0) + count
    return [{"label": label, "count": count} for label, count in sorted(totals.items())]


def _summary_lessons(value: object) -> dict[str, object]:
    source = value if isinstance(value, Mapping) else {}
    patterns: list[dict[str, object]] = []
    raw_patterns = source.get("top_patterns")
    if isinstance(raw_patterns, (list, tuple)):
        for raw in raw_patterns:
            if not isinstance(raw, Mapping):
                continue
            capability = _CAPABILITY_LABELS.get(
                str(raw.get("cartridge_id") or "").casefold()
            )
            if capability is None:
                continue
            patterns.append(
                {
                    "capability": capability,
                    "count": raw.get("count"),
                    "avg_confidence": raw.get("avg_confidence"),
                    "latest_rule": raw.get("latest_rule"),
                    "last_seen_at": raw.get("last_seen_at"),
                }
            )
    return {
        "total": source.get("total"),
        "recent": source.get("recent"),
        "by_capability": _count_breakdown(
            source.get("by_cartridge"), _CAPABILITY_LABELS
        ),
        "top_patterns": patterns,
    }


class ControlRoomBusinessSummaryResponse(PublicProjectionModel):
    total_anomalies: int = 0
    by_severity: SummaryCounts = Field(default_factory=SummaryCounts)
    by_cartridge: list[SummaryBreakdown] = Field(default_factory=list)
    by_domain: list[SummaryBreakdown] = Field(default_factory=list)
    open_decisions: int = 0
    sources: list[SummaryBreakdown] = Field(default_factory=list)
    financial: SummaryFinancial = Field(default_factory=SummaryFinancial)
    thresholds: SummaryThresholds = Field(default_factory=SummaryThresholds)
    lessons: SummaryLessons = Field(default_factory=SummaryLessons)
    alerts: SummaryCounts = Field(default_factory=SummaryCounts)

    @classmethod
    def project(cls, value: object) -> ControlRoomBusinessSummaryResponse:
        raw = value if isinstance(value, Mapping) else {}
        source = {
            **raw,
            "by_cartridge": _count_breakdown(
                raw.get("by_cartridge"), _CAPABILITY_LABELS
            ),
            "by_domain": _count_breakdown(raw.get("by_domain"), _DOMAIN_LABELS),
            "sources": _business_sources(raw.get("sources")),
            "lessons": _summary_lessons(raw.get("lessons")),
        }
        return super().project(source)  # type: ignore[return-value]


__all__ = ("ControlRoomBusinessSummaryResponse",)
