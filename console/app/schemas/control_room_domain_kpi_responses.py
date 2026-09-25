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
    "ControlRoomSapB1LearningKpisResponse",
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


Number = float | int | None


class MarginTotalsRow(PublicProjectionModel):
    company: str | None = None
    value: Number = None
    pct: Number = None
    revenue: Number = None
    commission: Number = None


class MarginGroupTotals(PublicProjectionModel):
    value: Number = None
    pct: Number = None
    revenue: Number = None
    commission: Number = None


class MarginTotalsKpi(B1KpiMetricBase):
    currency: str | None = None
    group: MarginGroupTotals | None = None
    companies: list[MarginTotalsRow] = Field(default_factory=list)
    trailing_group_pct: Number = None


class DestroyerCompanyRow(PublicProjectionModel):
    company: str | None = None
    customers: int | None = None
    margin_lost: Number = None
    min_margin_pct: Number = None


class DestroyerRow(PublicProjectionModel):
    company: str | None = None
    customer: str | None = None
    margin_lost: Number = None
    margin_pct: Number = None
    min_margin_pct: Number = None
    revenue: Number = None


class DestroyersKpi(B1KpiMetricBase):
    customers: int | None = None
    margin_lost: Number = None
    by_company: list[DestroyerCompanyRow] = Field(default_factory=list)
    top: list[DestroyerRow] = Field(default_factory=list)


class ConcentrationRow(PublicProjectionModel):
    company: str | None = None
    share_pct: Number = None
    top_margin: Number = None
    top_customers: int | None = None


class ConcentrationKpi(B1KpiMetricBase):
    by_company: list[ConcentrationRow] = Field(default_factory=list)


class SellerRow(PublicProjectionModel):
    seller: str | None = None
    margin: Number = None
    margin_pct: Number = None
    contribution: Number = None
    revenue: Number = None


class SellerCompanyRow(PublicProjectionModel):
    company: str | None = None
    sellers: int | None = None
    margin: Number = None
    best_pct: Number = None
    worst_pct: Number = None
    sellers_detail: list[SellerRow] = Field(default_factory=list)


class SellerMarginKpi(B1KpiMetricBase):
    by_company: list[SellerCompanyRow] = Field(default_factory=list)


class FinanceReconciliationRow(PublicProjectionModel):
    company: str | None = None
    period: str | None = None
    indicator: str | None = None
    dimension: str | None = None
    key: str | None = None
    label: str | None = None
    unit: str | None = None
    finance_value: Number = None
    platform_value: Number = None
    delta_pct: Number = None
    venta_bruta: Number = None
    devoluciones_nc: Number = None
    descuentos_pie_factura: Number = None
    costo_aplicado: Number = None
    comision: Number = None


class FinanceReconciliationKpi(B1KpiMetricBase):
    rows: int | None = None
    within: int | None = None
    outside: int | None = None
    without_platform: int | None = None
    platform_only: int | None = None
    within_pct: Number = None
    periods: list[str] = Field(default_factory=list)
    outliers: list[FinanceReconciliationRow] = Field(default_factory=list)
    ledger_months: int | None = None
    ledger_differences: int | None = None


class DataQualityRow(PublicProjectionModel):
    company: str | None = None
    check: str | None = None
    group: str | None = None
    total: int | None = None
    failing: int | None = None
    pct_ok: Number = None
    min_pct: Number = None


class DataQualityKpi(B1KpiMetricBase):
    checks: int | None = None
    checks_below_min: int | None = None
    failing: list[DataQualityRow] = Field(default_factory=list)


class EntityModelRow(PublicProjectionModel):
    entity: str | None = None
    company: str | None = None
    records: int | None = None
    identities: int | None = None
    shared_identities: int | None = None
    complete_records: int | None = None
    completeness_pct: Number = None
    orphans: int | None = None
    relation_rule: str | None = None


class EntityModelKpi(B1KpiMetricBase):
    entities: list[EntityModelRow] = Field(default_factory=list)


class SapB1MarginMetrics(PublicProjectionModel):
    margen_bruto: MarginTotalsKpi = Field(default_factory=MarginTotalsKpi)
    margen_contribucion: MarginTotalsKpi = Field(default_factory=MarginTotalsKpi)
    destructores: DestroyersKpi = Field(default_factory=DestroyersKpi)
    concentracion_top20: ConcentrationKpi = Field(default_factory=ConcentrationKpi)
    margen_vendedor: SellerMarginKpi = Field(default_factory=SellerMarginKpi)
    reconciliacion_finanzas: FinanceReconciliationKpi = Field(default_factory=FinanceReconciliationKpi)
    calidad_datos: DataQualityKpi = Field(default_factory=DataQualityKpi)
    modelo_entidades: EntityModelKpi = Field(default_factory=EntityModelKpi)


class ControlRoomSapB1MarginKpisResponse(DomainKpisBase):
    metrics: SapB1MarginMetrics = Field(default_factory=SapB1MarginMetrics)


class DistributorRow(PublicProjectionModel):
    distributor: str | None = None
    sell_out_revenue: Number = None
    sell_out_qty: Number = None
    sell_in_qty: Number = None
    growth_mom_pct: Number = None
    growth_yoy_pct: Number = None
    sellout_sellin_3m_pct: Number = None
    channel_days: Number = None
    margin_pct: Number = None
    expiry_exposed_pct: Number = None
    colors: dict[str, str | None] = Field(default_factory=dict)
    overall_color: str | None = None


class DistributorScorecardKpi(B1KpiMetricBase):
    distributors: list[DistributorRow] = Field(default_factory=list)
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_thresholds: int | None = None


class DistributorMetricRow(PublicProjectionModel):
    distributor: str | None = None
    value: Number = None
    color: str | None = None


class DistributorMetricKpi(B1KpiMetricBase):
    distributors: list[DistributorMetricRow] = Field(default_factory=list)


class ClinicRow(PublicProjectionModel):
    clinic: str | None = None
    revenue: Number = None
    units: Number = None
    share_pct: Number = None


class ClinicDistributorRow(PublicProjectionModel):
    distributor: str | None = None
    clinics: int | None = None
    revenue: Number = None
    units: Number = None
    top_share_pct: Number = None
    currency: str | None = None
    top: list[ClinicRow] = Field(default_factory=list)


class ClinicSellOutKpi(B1KpiMetricBase):
    by_distributor: list[ClinicDistributorRow] = Field(default_factory=list)


class SapB1SalesMetrics(PublicProjectionModel):
    ratio_sellout_sellin: DistributorMetricKpi = Field(default_factory=DistributorMetricKpi)
    dias_inventario: DistributorMetricKpi = Field(default_factory=DistributorMetricKpi)
    sellout_clinica: ClinicSellOutKpi = Field(default_factory=ClinicSellOutKpi)
    semaforo_distribuidoras: DistributorScorecardKpi = Field(default_factory=DistributorScorecardKpi)


class ControlRoomSapB1SalesKpisResponse(DomainKpisBase):
    metrics: SapB1SalesMetrics = Field(default_factory=SapB1SalesMetrics)


class ExpiryLevel(PublicProjectionModel):
    batches: int | None = None
    value: Number = None
    at_risk_value: Number = None


class ExpiryPriorityRow(PublicProjectionModel):
    company: str | None = None
    item: str | None = None
    batch: str | None = None
    branch: str | None = None
    level: str | None = None
    days_to_expiry: int | None = None
    at_risk_qty: Number = None
    at_risk_value: Number = None
    option: str | None = None
    action: str | None = None


class BatchExpiryKpi(B1KpiMetricBase):
    as_of: str | None = None
    levels: dict[str, ExpiryLevel] = Field(default_factory=dict)
    at_risk_value: Number = None
    options: dict[str, int] = Field(default_factory=dict)
    priorities: list[ExpiryPriorityRow] = Field(default_factory=list)


class SapB1ExpiryMetrics(PublicProjectionModel):
    caducidad_lotes: BatchExpiryKpi = Field(default_factory=BatchExpiryKpi)


class ControlRoomSapB1ExpiryKpisResponse(DomainKpisBase):
    metrics: SapB1ExpiryMetrics = Field(default_factory=SapB1ExpiryMetrics)


class CoverageRiskRow(PublicProjectionModel):
    company: str | None = None
    item: str | None = None
    name: str | None = None
    color: str | None = None
    stockout_risk: bool | None = None
    coverage_days: Number = None
    lead_time_days: int | None = None
    stockout_date: str | None = None
    suggested_qty: Number = None
    action: str | None = None
    supplier: str | None = None
    alternate_supplier: str | None = None
    order_by: str | None = None
    basis: str | None = None
    critical: bool | None = None
    plan_changed: bool | None = None
    options: list[str] = Field(default_factory=list)


class CoverageKpi(B1KpiMetricBase):
    as_of: str | None = None
    colors: dict[str, int] = Field(default_factory=dict)
    stockout_risk: int | None = None
    critical_at_risk: int | None = None
    plan_changes: int | None = None
    suggestions: int | None = None
    suggested_value: Number = None
    risks: list[CoverageRiskRow] = Field(default_factory=list)


class PurchaseNeedRow(PublicProjectionModel):
    company: str | None = None
    item: str | None = None
    name: str | None = None
    net_need_qty: Number = None
    open_po_qty: Number = None
    coverage_pct: Number = None
    critical: bool | None = None


class PurchaseNeedKpi(B1KpiMetricBase):
    items_with_need: int | None = None
    items_short: int | None = None
    shortfalls: list[PurchaseNeedRow] = Field(default_factory=list)


class CostVarianceRow(PublicProjectionModel):
    company: str | None = None
    item: str | None = None
    name: str | None = None
    variance_pct: Number = None
    variance_value: Number = None
    raw_material: bool | None = None


class CostVarianceKpi(B1KpiMetricBase):
    items: int | None = None
    above_threshold: int | None = None
    variance_value: Number = None
    worst: list[CostVarianceRow] = Field(default_factory=list)


class LateSupplierRow(PublicProjectionModel):
    company: str | None = None
    supplier: str | None = None
    intercompany: bool | None = None
    receipts: int | None = None
    late_receipts: int | None = None
    on_time_pct: Number = None
    avg_lead_days: Number = None
    avg_promised_days: Number = None
    max_delay_days: int | None = None


class SupplierLeadTimeKpi(B1KpiMetricBase):
    suppliers: int | None = None
    late_suppliers: list[LateSupplierRow] = Field(default_factory=list)


class SapB1SupplyMetrics(PublicProjectionModel):
    dias_cobertura: CoverageKpi = Field(default_factory=CoverageKpi)
    oc_vs_necesidad: PurchaseNeedKpi = Field(default_factory=PurchaseNeedKpi)
    costo_real_vs_estandar: CostVarianceKpi = Field(default_factory=CostVarianceKpi)
    lead_time_proveedores: SupplierLeadTimeKpi = Field(default_factory=SupplierLeadTimeKpi)


class ControlRoomSapB1SupplyKpisResponse(DomainKpisBase):
    metrics: SapB1SupplyMetrics = Field(default_factory=SapB1SupplyMetrics)


class LearningSourceRow(PublicProjectionModel):
    source: str | None = None
    alerts: int | None = None
    decisions: int | None = None
    outcomes: int | None = None
    false_positives: int | None = None
    achieved: int | None = None
    not_achieved: int | None = None


class LearningSuggestionRow(PublicProjectionModel):
    source: str | None = None
    thresholds: list[str] = Field(default_factory=list)
    reason: str | None = None


class LearningKpi(B1KpiMetricBase):
    window_days: int | None = None
    sources: list[LearningSourceRow] = Field(default_factory=list)
    suggestions: list[LearningSuggestionRow] = Field(default_factory=list)


class SapB1LearningMetrics(PublicProjectionModel):
    aprendizaje: LearningKpi = Field(default_factory=LearningKpi)


class ControlRoomSapB1LearningKpisResponse(DomainKpisBase):
    metrics: SapB1LearningMetrics = Field(default_factory=SapB1LearningMetrics)


class SapB1SemaforoMetrics(PublicProjectionModel):
    margen_bruto: MarginTotalsKpi = Field(default_factory=MarginTotalsKpi)
    destructores: DestroyersKpi = Field(default_factory=DestroyersKpi)
    reconciliacion_finanzas: FinanceReconciliationKpi = Field(default_factory=FinanceReconciliationKpi)
    semaforo_distribuidoras: DistributorScorecardKpi = Field(default_factory=DistributorScorecardKpi)
    caducidad_lotes: BatchExpiryKpi = Field(default_factory=BatchExpiryKpi)
    dias_cobertura: CoverageKpi = Field(default_factory=CoverageKpi)
    calidad_datos: DataQualityKpi = Field(default_factory=DataQualityKpi)


class ControlRoomSapB1SemaforoKpisResponse(DomainKpisBase):
    metrics: SapB1SemaforoMetrics = Field(default_factory=SapB1SemaforoMetrics)
