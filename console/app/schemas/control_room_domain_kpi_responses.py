from __future__ import annotations

from pydantic import Field

from app.schemas.control_room_public_projection import PublicProjectionModel


class KpiEvidenceFilters(PublicProjectionModel):
    window_start: str | None = None
    window_end_exclusive: str | None = None
    months: int | None = None
    top_n: int | None = None
    before_month: str | None = None
    period: str | None = None
    as_of: str | None = None
    sla_hours: float | int | None = None
    sla_source: str | None = None
    sla_threshold: str | None = None
    windows_days: list[int] = Field(default_factory=list)
    sentinel_excluded_from: str | None = None
    active_only: bool | None = None
    snapshot: str | None = None
    status: str | None = None
    breakdown_limit: int | None = None
    failing_entities_top_n: int | None = None
    base_currency: str | None = None
    original_currencies: list[str] = Field(default_factory=list)


class KpiEvidenceRef(PublicProjectionModel):
    metric: str | None = None
    type: str | None = None
    source: str | None = None
    generation: int | None = None
    published_at: str | None = None
    partial_source: bool | None = None
    filters: KpiEvidenceFilters = Field(default_factory=KpiEvidenceFilters)


class KpiMetricBase(PublicProjectionModel):
    status: str | None = None
    supported: bool | None = None
    proxy_note: str | None = None
    error: str | None = None
    evidence_refs: list[KpiEvidenceRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DomainKpisBase(PublicProjectionModel):
    domain: str | None = None
    generated_at: str | None = None
    status: str | None = None
    named_rows: int = 0
    evidence_refs: list[KpiEvidenceRef] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    unavailable_metrics: list[str] = Field(default_factory=list)
    degraded_metrics: list[str] = Field(default_factory=list)


class ProjectHoursRow(PublicProjectionModel):
    proyecto: str | None = None
    project_name: str | None = None
    cliente: str | None = None
    billable_hours: float | int | None = None
    billable_amount_usd: float | int | None = None


class BillableHoursLoggedKpi(KpiMetricBase):
    window_start: str | None = None
    window_end: str | None = None
    months: int | None = None
    billable_hours: float | int | None = None
    billable_amount_usd: float | int | None = None
    projects_affected: int | None = None
    contributors: int | None = None
    top_projects: list[ProjectHoursRow] = Field(default_factory=list)


class DepartmentCostRow(PublicProjectionModel):
    departamento: str | None = None
    headcount: int | None = None
    hours: float | int | None = None
    cost: float | int | None = None
    sunk_cost: float | int | None = None
    potential_cost: float | int | None = None


class LaborCostByDepartmentKpi(KpiMetricBase):
    period: str | None = None
    departments_count: int | None = None
    headcount: int | None = None
    total_hours: float | int | None = None
    total_cost: float | int | None = None
    total_sunk_cost: float | int | None = None
    total_potential_cost: float | int | None = None
    departments: list[DepartmentCostRow] = Field(default_factory=list)


class ProjectMarginRow(PublicProjectionModel):
    proyecto: str | None = None
    project_name: str | None = None
    cliente: str | None = None
    tipo_proyecto: str | None = None
    revenue_base: float | int | None = None
    cost_direct: float | int | None = None
    cost_sunk: float | int | None = None
    margin: float | int | None = None
    margin_pct: float | int | None = None
    billable_hours: float | int | None = None


class ProjectMarginKpi(KpiMetricBase):
    window_start: str | None = None
    window_end: str | None = None
    months: int | None = None
    projects_count: int | None = None
    total_revenue_base: float | int | None = None
    total_cost_direct: float | int | None = None
    total_cost_sunk: float | int | None = None
    total_margin: float | int | None = None
    total_margin_pct: float | int | None = None
    total_original_billing: float | int | None = None
    original_currencies: list[str] = Field(default_factory=list)
    top_projects: list[ProjectMarginRow] = Field(default_factory=list)
    bottom_projects: list[ProjectMarginRow] = Field(default_factory=list)


class FinanceMetrics(PublicProjectionModel):
    billable_hours_logged: BillableHoursLoggedKpi = Field(
        default_factory=BillableHoursLoggedKpi
    )
    labor_cost_by_department: LaborCostByDepartmentKpi = Field(
        default_factory=LaborCostByDepartmentKpi
    )
    project_margin: ProjectMarginKpi = Field(default_factory=ProjectMarginKpi)


class ControlRoomFinanceKpisResponse(DomainKpisBase):
    metrics: FinanceMetrics = Field(default_factory=FinanceMetrics)


class RunTotals(PublicProjectionModel):
    failed_24h: int | None = None
    success_24h: int | None = None
    failed_7d: int | None = None
    success_7d: int | None = None
    partial_7d: int | None = None


class CartridgeHealthRow(PublicProjectionModel):
    cartridge_id: str | None = None
    cartridge: str | None = None
    failed_24h: int | None = None
    success_24h: int | None = None
    failed_7d: int | None = None
    success_7d: int | None = None
    partial_7d: int | None = None
    failure_rate_7d: float | int | None = None
    last_failed_at: str | None = None
    sources: list[str] = Field(default_factory=list)


class FailingEntityRow(PublicProjectionModel):
    cartridge_id: str | None = None
    cartridge: str | None = None
    entity: str | None = None
    failures_7d: int | None = None
    failures_24h: int | None = None
    last_failed_at: str | None = None
    sources: list[str] = Field(default_factory=list)


class PipelineHealthKpi(KpiMetricBase):
    as_of: str | None = None
    window_24h_start: str | None = None
    window_7d_start: str | None = None
    cartridges: list[CartridgeHealthRow] = Field(default_factory=list)
    totals: RunTotals = Field(default_factory=RunTotals)
    cartridges_count: int | None = None
    cartridges_with_failures_24h: int | None = None
    failing_entities: list[FailingEntityRow] = Field(default_factory=list)


class CartridgeFreshnessRow(PublicProjectionModel):
    cartridge_id: str | None = None
    cartridge: str | None = None
    last_success_at: str | None = None
    success_runs: int | None = None
    hours_since_success: float | int | None = None
    exceeds_sla: bool | None = None
    sources: list[str] = Field(default_factory=list)


class DataFreshnessKpi(KpiMetricBase):
    as_of: str | None = None
    sla_hours: float | int | None = None
    sla_source: str | None = None
    cartridges: list[CartridgeFreshnessRow] = Field(default_factory=list)
    cartridges_count: int | None = None
    exceeding_sla: int | None = None


class AbsenceTypeRow(PublicProjectionModel):
    absence_type: str | None = None
    days_workable: float | int | None = None
    employees_affected: int | None = None
    absence_records: int | None = None
    rate: float | int | None = None


class AbsenceRateKpi(KpiMetricBase):
    period: str | None = None
    working_days: int | None = None
    headcount: int | None = None
    total_days_workable: float | int | None = None
    absence_rate: float | int | None = None
    types_count: int | None = None
    by_type: list[AbsenceTypeRow] = Field(default_factory=list)


class OperationsMetrics(PublicProjectionModel):
    pipeline_health: PipelineHealthKpi = Field(default_factory=PipelineHealthKpi)
    data_freshness_by_cartridge: DataFreshnessKpi = Field(
        default_factory=DataFreshnessKpi
    )
    absence_rate_company_by_type: AbsenceRateKpi = Field(default_factory=AbsenceRateKpi)


class ControlRoomOperationsKpisResponse(DomainKpisBase):
    metrics: OperationsMetrics = Field(default_factory=OperationsMetrics)


class DepartmentRiskRow(PublicProjectionModel):
    department_name: str | None = None
    total: int | None = None
    high: int | None = None
    medium: int | None = None


class TalentActionRef(PublicProjectionModel):
    action_id: str | None = None
    affected_count: int | None = None
    severity: str | None = None
    present: bool | None = None


class AttritionRiskKpi(KpiMetricBase):
    total: int | None = None
    high: int | None = None
    medium: int | None = None
    low: int | None = None
    insufficient_data: int | None = None
    avg_score_valid: float | int | None = None
    departments_top: list[DepartmentRiskRow] = Field(default_factory=list)
    talent_action: TalentActionRef | None = None


class DepartmentExpiryRow(PublicProjectionModel):
    department_name: str | None = None
    within_30: int | None = None
    within_60: int | None = None
    within_90: int | None = None


class EmploymentEndExpiryKpi(KpiMetricBase):
    as_of: str | None = None
    within_30: int | None = None
    within_60: int | None = None
    within_90: int | None = None
    active_filter_applied: bool | None = None
    sentinel_excluded_from: str | None = None
    departments_top: list[DepartmentExpiryRow] = Field(default_factory=list)


class DealBucketRow(PublicProjectionModel):
    bucket: str | None = None
    deals: int | None = None
    amount: float | int | None = None


class DealStageRow(PublicProjectionModel):
    stage_name: str | None = None
    deals: int | None = None
    amount: float | int | None = None


class DealReasonRow(PublicProjectionModel):
    risk_reason: str | None = None
    deals: int | None = None
    amount: float | int | None = None
    max_days_overdue: int | None = None


class NamedDealRow(PublicProjectionModel):
    opportunity_name: str | None = None
    stage_name: str | None = None
    amount: float | int | None = None
    close_date: str | None = None
    days_overdue: int | None = None
    risk_reason: str | None = None


class DealSlippageKpi(KpiMetricBase):
    as_of: str | None = None
    deals: int | None = None
    amount_total: float | int | None = None
    buckets: list[DealBucketRow] = Field(default_factory=list)
    by_stage: list[DealStageRow] = Field(default_factory=list)
    by_reason: list[DealReasonRow] = Field(default_factory=list)
    top_deals: list[NamedDealRow] = Field(default_factory=list)


class RiskMetrics(PublicProjectionModel):
    attrition_risk_population: AttritionRiskKpi = Field(
        default_factory=AttritionRiskKpi
    )
    employment_end_expiry: EmploymentEndExpiryKpi = Field(
        default_factory=EmploymentEndExpiryKpi
    )
    deal_slippage: DealSlippageKpi = Field(default_factory=DealSlippageKpi)


class ControlRoomRiskKpisResponse(DomainKpisBase):
    metrics: RiskMetrics = Field(default_factory=RiskMetrics)


__all__ = (
    "ControlRoomFinanceKpisResponse",
    "ControlRoomOperationsKpisResponse",
    "ControlRoomRiskKpisResponse",
    "DomainKpisBase",
    "KpiEvidenceRef",
    "KpiMetricBase",
)
