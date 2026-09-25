from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime

from pydantic import Field

from app.schemas.control_room_public_projection import PublicProjectionModel
from app.services.control_room.business_evidence import has_evidence


_CAPABILITY_LABELS = {
    "sap_hcm": "Personal y nomina",
    "sap_successfactors": "Talento y organizacion",
    "sap_s4hana": "Finanzas y operaciones",
    "sap_b1": "Finanzas, ventas y compras (SAP Business One)",
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
    "riesgo": "Riesgo",
    "direccion": "Dirección",
}
_SOURCE_STATE_FIELDS = (
    "status",
    "source_status",
    "data_status",
    "data_readiness",
    "readiness_status",
    "evaluation_status",
)
_UNUSABLE_SOURCE_STATES = frozenset(
    {
        "blocked",
        "error",
        "failed",
        "failure",
        "invalid_schema",
        "missing",
        "no_permission",
        "partial",
        "schema_only",
        "stub",
        "unavailable",
    }
)
_SUCCESSFUL_SOURCE_STATES = frozenset(
    {"complete", "empty", "gold_ready", "materialized", "ok", "ready", "success"}
)
_SOURCE_OBSERVATION_DATE_FIELDS = (
    "observed_at",
    "observation_date",
    "materialized_at",
    "checked_at",
    "as_of",
    "snapshot_month",
)


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


def _source_states(item: Mapping[str, object]) -> set[str]:
    return {
        value.strip().casefold()
        for key in _SOURCE_STATE_FIELDS
        if isinstance((value := item.get(key)), str) and value.strip()
    }


def _valid_source_date(value: object) -> bool:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value <= datetime.now(UTC).date()
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(text) <= datetime.now(UTC).date()
            except ValueError:
                return False
    else:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


def _has_source_observation_date(item: Mapping[str, object]) -> bool:
    values = [
        item[key]
        for key in _SOURCE_OBSERVATION_DATE_FIELDS
        if key in item and item[key] is not None and item[key] != ""
    ]
    return bool(values) and all(_valid_source_date(value) for value in values)


def _business_source_count(item: Mapping[str, object]) -> int | None:
    count = item.get("count")
    if type(count) is not int or count < 0:
        return None
    states = _source_states(item)
    if states & _UNUSABLE_SOURCE_STATES or item.get("error"):
        return None
    if "empty" in states and count != 0:
        return None
    has_date = _has_source_observation_date(item)
    stale = "stale" in states or item.get("stale") is True
    if states - (_SUCCESSFUL_SOURCE_STATES | {"stale"}):
        return None
    if stale and (not has_date or not has_evidence(item)):
        return None
    if count == 0 and (not has_date or not states & _SUCCESSFUL_SOURCE_STATES):
        return None
    return count


def _business_sources(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    totals: dict[str, int] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        label = _CAPABILITY_LABELS.get(str(item.get("cartridge") or "").casefold())
        count = _business_source_count(item)
        if label is not None and count is not None:
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
