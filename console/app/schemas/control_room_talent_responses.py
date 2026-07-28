from __future__ import annotations

import re

from pydantic import Field, field_validator

from app.schemas.control_room_business_responses import PublicMetricRow
from app.schemas.control_room_public_projection import (
    PublicProjectionModel,
    PublicScalar,
    PublicSlugIdentity,
)
from app.schemas.control_room_talent_diagnostic_responses import (
    ControlRoomTalentMetadataReadinessResponse,
)


class TalentProfile(PublicProjectionModel):
    industry: str | None = None
    company_profile: str | None = None
    decision_mode: str | None = None
    compensation_enabled: bool | None = None
    write_back_enabled: bool | None = None


class TalentReadiness(PublicProjectionModel):
    ready_min: int = 0
    near_min: int = 0
    profiled_employees: int = 0
    calculable_employees: int = 0
    insufficient_data_employees: int = 0
    nine_box_available: int = 0
    roles_without_requirements: int = 0
    high_severity_signals: int = 0
    learning_blockers: int = 0
    recruiting_blockers: int = 0
    skill_gap_count: int = 0
    skill_coverage_pct: float | int | None = None
    confidence: float | int | None = None
    status: str | None = None


class TalentWidget(PublicSlugIdentity):
    title: str | None = None
    value: PublicScalar = None
    contractor_count: int | None = None
    risk_factor: float | int | None = None
    status: str | None = None
    rows: list[PublicMetricRow] = Field(default_factory=list)


class TalentSignal(PublicSlugIdentity):
    severity: str | None = None
    title: str | None = None
    affected_count: int = 0
    recommendation: str | None = None
    status: str | None = None


class TalentBlocker(PublicSlugIdentity):
    status: str | None = None
    title: str | None = None


class WorkforceKpis(PublicProjectionModel):
    active_headcount: int | None = None
    avg_tenure_months: float | int | None = None
    attrition_rate: float | int | None = None
    history_months: int | None = None


class WorkforceSeries(PublicProjectionModel):
    months: list[str] = Field(default_factory=list)
    headcount: list[float | int | None] = Field(default_factory=list)
    avg_tenure_months: list[float | int | None] = Field(default_factory=list)
    attrition_rate: list[float | int | None] = Field(default_factory=list)


class ControlRoomTalentWorkforceTrendsResponse(PublicProjectionModel):
    status: str | None = None
    kpis: WorkforceKpis = Field(default_factory=WorkforceKpis)
    series: WorkforceSeries = Field(default_factory=WorkforceSeries)


class ControlRoomTalentKpisResponse(PublicProjectionModel):
    generated_at: str | None = None
    profile: TalentProfile = Field(default_factory=TalentProfile)
    readiness: TalentReadiness = Field(default_factory=TalentReadiness)
    widgets: list[TalentWidget] = Field(default_factory=list)
    signals: list[TalentSignal] = Field(default_factory=list)
    blockers: list[TalentBlocker] = Field(default_factory=list)
    workforce_trends: ControlRoomTalentWorkforceTrendsResponse | None = None


class TalentNineBoxTotals(PublicProjectionModel):
    employees: int = 0
    ready: int = 0
    reference: int = 0
    blocked: int = 0
    cells: int = 0


class TalentNineBoxCell(PublicProjectionModel):
    box_id: str | None = None
    box_label: str | None = None
    potential_band: str | None = None
    performance_band: str | None = None
    movement_action: str | None = None
    display_order: int = 0
    employee_count: int = 0
    ready_count: int = 0
    cpa_real_count: int = 0
    reference_count: int = 0
    blocked_count: int = 0
    status: str | None = None

    @field_validator("box_id")
    @classmethod
    def validate_box_id(cls, value: str | None) -> str | None:
        return value if value and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", value) else None


class TalentPerformanceCounts(PublicProjectionModel):
    high: int = 0
    medium: int = 0
    low: int = 0


class TalentPerformanceRow(PublicProjectionModel):
    employee_key: str | None = None
    display_name: str | None = None
    role: str | None = None
    unit: str | None = None
    performance_band_available: str | None = None
    potential_pending: bool | None = None
    fit_band: str | None = None

    @field_validator("employee_key")
    @classmethod
    def validate_employee_key(cls, value: str | None) -> str | None:
        return value if value and re.fullmatch(r"tal_[0-9a-f]{12}", value) else None


class TalentPerformanceCohort(PublicProjectionModel):
    count: int = 0
    band_counts: TalentPerformanceCounts = Field(
        default_factory=TalentPerformanceCounts
    )
    roster: list[TalentPerformanceRow] = Field(default_factory=list)
    roster_truncated: bool | None = None


class TalentPrivacy(PublicProjectionModel):
    masked: bool | None = None
    roster: str | None = None
    forbidden_fields: list[str] = Field(default_factory=list)
    excluded_fields: list[str] = Field(default_factory=list)


class ControlRoomTalentNineBoxResponse(PublicProjectionModel):
    generated_at: str | None = None
    status: str = ""
    totals: TalentNineBoxTotals = Field(default_factory=TalentNineBoxTotals)
    cells: list[TalentNineBoxCell] = Field(default_factory=list)
    desempeno_disponible: TalentPerformanceCohort | None = None
    blockers: list[TalentBlocker] = Field(default_factory=list)
    privacy: TalentPrivacy | None = None


class TalentBox(PublicProjectionModel):
    box_id: str | None = None
    box_label: str | None = None
    potential_band: str | None = None
    performance_band: str | None = None
    movement_action: str | None = None
    display_order: int = 0

    @field_validator("box_id")
    @classmethod
    def validate_box_id(cls, value: str | None) -> str | None:
        return value if value and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", value) else None


class TalentRosterRow(PublicProjectionModel):
    employee_key: str | None = None
    display_name: str | None = None
    role: str | None = None
    unit: str | None = None
    region: str | None = None
    box_label: str | None = None
    performance_band: str | None = None
    performance_band_available: str | None = None
    potential_pending: bool | None = None
    desempeno_disponible: bool | None = None
    potential_band: str | None = None
    fit_band: str | None = None
    movement_age_bucket: str | None = None
    data_status: str | None = None

    @field_validator("employee_key")
    @classmethod
    def validate_employee_key(cls, value: str | None) -> str | None:
        return value if value and re.fullmatch(r"tal_[0-9a-f]{12}", value) else None


class ControlRoomTalentRosterResponse(PublicProjectionModel):
    generated_at: str | None = None
    box: TalentBox = Field(default_factory=TalentBox)
    status: str = ""
    count: int = 0
    roster: list[TalentRosterRow] = Field(default_factory=list)
    blockers: list[TalentBlocker] = Field(default_factory=list)
    privacy: TalentPrivacy | None = None


class TalentAnomalySummary(PublicProjectionModel):
    total: int = 0
    high: int = 0
    recommendation_only: int = 0


class TalentAnomaly(TalentSignal):
    pass


class ControlRoomTalentAnomaliesResponse(PublicProjectionModel):
    generated_at: str | None = None
    status: str = ""
    summary: TalentAnomalySummary = Field(default_factory=TalentAnomalySummary)
    items: list[TalentAnomaly] = Field(default_factory=list)
    blockers: list[TalentBlocker] = Field(default_factory=list)


class TalentOverviewNineBox(PublicProjectionModel):
    status: str | None = None
    totals: TalentNineBoxTotals = Field(default_factory=TalentNineBoxTotals)
    cells: list[TalentNineBoxCell] = Field(default_factory=list)
    blockers: list[TalentBlocker] = Field(default_factory=list)


class TalentOverviewAnomalies(PublicProjectionModel):
    status: str | None = None
    summary: TalentAnomalySummary = Field(default_factory=TalentAnomalySummary)
    items: list[TalentAnomaly] = Field(default_factory=list)


class ControlRoomTalentOverviewResponse(ControlRoomTalentKpisResponse):
    nine_box: TalentOverviewNineBox = Field(default_factory=TalentOverviewNineBox)
    anomalies: TalentOverviewAnomalies = Field(default_factory=TalentOverviewAnomalies)


__all__ = tuple(name for name in globals() if name.startswith("ControlRoom"))
