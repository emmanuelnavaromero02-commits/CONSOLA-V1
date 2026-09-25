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
    "ControlRoomSapB1ExpiryKpisResponse",
    "ControlRoomSapB1MarginKpisResponse",
    "ControlRoomSapB1SalesKpisResponse",
    "ControlRoomSapB1SemaforoKpisResponse",
    "ControlRoomSapB1SupplyKpisResponse",
    "DomainKpisBase",
    "KpiEvidenceRef",
    "KpiMetricBase",
)


class B1KpiMetricBase(KpiMetricBase):
    period: str | None = None
    breaches: list[str] = Field(default_factory=list)


class GroupMarginKpi(B1KpiMetricBase):
    currency: str | None = None
    external_revenue: float | int | None = None
    external_gross_profit: float | int | None = None
    consolidated_gross_profit: float | int | None = None
    external_margin_pct: float | int | None = None
    consolidated_margin_pct: float | int | None = None
    unrealized_profit_change: float | int | None = None
    trailing_margin_pct: float | int | None = None
    currencies: list[str] = Field(default_factory=list)


class CompanyMarginRow(PublicProjectionModel):
    company: str | None = None
    revenue: float | int | None = None
    gross_profit: float | int | None = None
    margin_pct: float | int | None = None
    min_margin_pct: float | int | None = None


class CompanyMarginKpi(B1KpiMetricBase):
    companies: list[CompanyMarginRow] = Field(default_factory=list)


class CustomerMarginRow(PublicProjectionModel):
    company: str | None = None
    customer: str | None = None
    revenue: float | int | None = None
    margin_pct: float | int | None = None


class CustomerMarginKpi(B1KpiMetricBase):
    customers: int | None = None
    customers_below_min: int | None = None
    customers_negative: int | None = None
    revenue: float | int | None = None
    revenue_below_min_pct: float | int | None = None
    worst_customers: list[CustomerMarginRow] = Field(default_factory=list)


class ItemFamilyMarginRow(PublicProjectionModel):
    family: str | None = None
    revenue: float | int | None = None
    gross_profit: float | int | None = None
    margin_pct: float | int | None = None
    mix_pct: float | int | None = None


class ItemFamilyMarginKpi(B1KpiMetricBase):
    families: list[ItemFamilyMarginRow] = Field(default_factory=list)


class BelowMinSalesKpi(B1KpiMetricBase):
    revenue: float | int | None = None
    below_min_revenue: float | int | None = None
    below_min_pct: float | int | None = None
    below_cost_revenue: float | int | None = None
    below_cost_pct: float | int | None = None


class ReconciliationRow(PublicProjectionModel):
    company: str | None = None
    period: str | None = None
    revenue_diff_pct: float | int | None = None
    cogs_diff_pct: float | int | None = None
    gross_profit_diff_pct: float | int | None = None
    tolerance_pct: float | int | None = None


class ReconciliationKpi(B1KpiMetricBase):
    months: int | None = None
    company_months: int | None = None
    within_tolerance: int | None = None
    out_of_tolerance: int | None = None
    without_controls: int | None = None
    outliers: list[ReconciliationRow] = Field(default_factory=list)


class DataQualityRow(PublicProjectionModel):
    company: str | None = None
    check: str | None = None
    group: str | None = None
    total: int | None = None
    failing: int | None = None
    pct_ok: float | int | None = None
    min_pct: float | int | None = None


class DataQualityKpi(B1KpiMetricBase):
    checks: int | None = None
    checks_below_min: int | None = None
    failing: list[DataQualityRow] = Field(default_factory=list)


class SapB1MarginMetrics(PublicProjectionModel):
    group_margin: GroupMarginKpi = Field(default_factory=GroupMarginKpi)
    company_margin: CompanyMarginKpi = Field(default_factory=CompanyMarginKpi)
    customer_margin: CustomerMarginKpi = Field(default_factory=CustomerMarginKpi)
    item_family_margin: ItemFamilyMarginKpi = Field(default_factory=ItemFamilyMarginKpi)
    below_min_sales: BelowMinSalesKpi = Field(default_factory=BelowMinSalesKpi)
    reconciliation: ReconciliationKpi = Field(default_factory=ReconciliationKpi)
    data_quality: DataQualityKpi = Field(default_factory=DataQualityKpi)


class ControlRoomSapB1MarginKpisResponse(DomainKpisBase):
    metrics: SapB1MarginMetrics = Field(default_factory=SapB1MarginMetrics)


class DistributorRow(PublicProjectionModel):
    distributor: str | None = None
    sell_out_revenue: float | int | None = None
    sell_out_qty: float | int | None = None
    sell_in_qty: float | int | None = None
    growth_mom_pct: float | int | None = None
    growth_yoy_pct: float | int | None = None
    sell_through_3m_pct: float | int | None = None
    channel_days: float | int | None = None
    margin_pct: float | int | None = None
    expiry_exposed_pct: float | int | None = None
    overall_color: str | None = None


class DistributorScorecardKpi(B1KpiMetricBase):
    distributors: list[DistributorRow] = Field(default_factory=list)
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_thresholds: int | None = None
    sell_in_qty: float | int | None = None
    sell_out_qty: float | int | None = None


class SapB1SalesMetrics(PublicProjectionModel):
    distributor_scorecard: DistributorScorecardKpi = Field(default_factory=DistributorScorecardKpi)


class ControlRoomSapB1SalesKpisResponse(DomainKpisBase):
    metrics: SapB1SalesMetrics = Field(default_factory=SapB1SalesMetrics)


class ExpiryCompanyRow(PublicProjectionModel):
    company: str | None = None
    expired_qty: float | int | None = None
    expired_value: float | int | None = None
    horizon_value: float | int | None = None
    at_risk_value: float | int | None = None
    transfer_candidates: int | None = None


class ExpiryItemRow(PublicProjectionModel):
    company: str | None = None
    item: str | None = None
    at_risk_qty: float | int | None = None
    at_risk_value: float | int | None = None
    action: str | None = None


class BatchExpiryKpi(B1KpiMetricBase):
    as_of: str | None = None
    expired_qty: float | int | None = None
    expired_value: float | int | None = None
    horizon_value: float | int | None = None
    at_risk_value: float | int | None = None
    transfer_candidates: int | None = None
    by_company: list[ExpiryCompanyRow] = Field(default_factory=list)
    top_items: list[ExpiryItemRow] = Field(default_factory=list)


class SapB1ExpiryMetrics(PublicProjectionModel):
    batch_expiry: BatchExpiryKpi = Field(default_factory=BatchExpiryKpi)


class ControlRoomSapB1ExpiryKpisResponse(DomainKpisBase):
    metrics: SapB1ExpiryMetrics = Field(default_factory=SapB1ExpiryMetrics)


class CoverageCompanyRow(PublicProjectionModel):
    company: str | None = None
    items: int | None = None
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_consumption: int | None = None
    suggestions: int | None = None
    suggested_value: float | int | None = None
    median_coverage_days: float | int | None = None
    median_coverage_with_orders_days: float | int | None = None
    min_stock_outdated: int | None = None


class CoverageRiskRow(PublicProjectionModel):
    company: str | None = None
    item: str | None = None
    color: str | None = None
    coverage_days: float | int | None = None
    coverage_with_orders_days: float | int | None = None
    lead_time_days: int | None = None
    stockout_date: str | None = None
    suggested_qty: float | int | None = None
    action: str | None = None
    order_by: str | None = None
    suggested_value: float | int | None = None


class ItemCoverageKpi(B1KpiMetricBase):
    as_of: str | None = None
    items: int | None = None
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_consumption: int | None = None
    suggestions: int | None = None
    suggested_value: float | int | None = None
    min_stock_outdated: int | None = None
    by_company: list[CoverageCompanyRow] = Field(default_factory=list)
    top_risks: list[CoverageRiskRow] = Field(default_factory=list)


class SapB1SupplyMetrics(PublicProjectionModel):
    item_coverage: ItemCoverageKpi = Field(default_factory=ItemCoverageKpi)


class ControlRoomSapB1SupplyKpisResponse(DomainKpisBase):
    metrics: SapB1SupplyMetrics = Field(default_factory=SapB1SupplyMetrics)


class SapB1SemaforoMetrics(PublicProjectionModel):
    group_margin: GroupMarginKpi = Field(default_factory=GroupMarginKpi)
    company_margin: CompanyMarginKpi = Field(default_factory=CompanyMarginKpi)
    distributor_scorecard: DistributorScorecardKpi = Field(default_factory=DistributorScorecardKpi)
    batch_expiry: BatchExpiryKpi = Field(default_factory=BatchExpiryKpi)
    item_coverage: ItemCoverageKpi = Field(default_factory=ItemCoverageKpi)
    data_quality: DataQualityKpi = Field(default_factory=DataQualityKpi)


class ControlRoomSapB1SemaforoKpisResponse(DomainKpisBase):
    metrics: SapB1SemaforoMetrics = Field(default_factory=SapB1SemaforoMetrics)
