from __future__ import annotations

from pydantic import Field

from app.schemas.control_room_public_projection import (
    PublicProjectionModel,
    PublicScalar,
    PublicSlugIdentity,
)


class PublicStatusCounts(PublicProjectionModel):
    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    open: int = 0
    in_review: int = 0
    approved: int = 0
    dismissed: int = 0
    resolved: int = 0
    ready: int = 0
    partial: int = 0
    blocked: int = 0
    empty: int = 0
    missing: int = 0
    unavailable: int = 0
    invalid_schema: int = 0
    no_permission: int = 0


class PublicSource(PublicProjectionModel):
    cartridge: str | None = None
    module: str | None = None
    domain: str | None = None
    status: str | None = None
    count: int = 0
    data_readiness: str | None = None
    operationally_ready: bool | None = None
    checked_at: str | None = None


class PublicMetricRow(PublicProjectionModel):
    label: str | None = None
    value: PublicScalar = None
    count: int | None = None
    headcount: int | None = None
    contractor_count: int | None = None
    risk_factor: float | int | None = None
    percentage: float | int | None = None
    rate: float | int | None = None
    status: str | None = None
    fact: str | None = None


class PublicImpactDriver(PublicProjectionModel):
    label: str | None = None
    value: PublicScalar = None
    currency: str | None = None
    unit: str | None = None
    points: float | int | None = None


class PublicDecisionIntelligence(PublicProjectionModel):
    method: str | None = None
    anomaly_probability: float | int | None = None
    uncertainty_level: str | None = None
    recommended_decision: str | None = None
    recommended_next_step: str | None = None
    rationale: str | None = None
    data_quality_status: str | None = None


class PublicOmegaOption(PublicProjectionModel):
    id: str | None = None
    label: str | None = None
    score: float | int | None = None
    risk: str | None = None
    recommendation: str | None = None
    selected: bool | None = None


class PublicOmegaState(PublicProjectionModel):
    status: str | None = None
    label: str | None = None


class PublicOmega(PublicProjectionModel):
    step: str | None = None
    options: list[PublicOmegaOption] = Field(default_factory=list)
    decision: PublicOmegaState = Field(default_factory=PublicOmegaState)
    execution: PublicOmegaState = Field(default_factory=PublicOmegaState)
    control: PublicOmegaState = Field(default_factory=PublicOmegaState)
    decision_intelligence: PublicDecisionIntelligence = Field(
        default_factory=PublicDecisionIntelligence
    )


class PublicBusinessItem(PublicProjectionModel):
    id: str = ""
    kind: str = ""
    title: str | None = None
    description: str | None = None
    severity: str | None = None
    status: str | None = None
    recommendation: str | None = None
    root_cause: str | None = None
    impact: str | None = None
    detected_at: str | None = None
    domain: str | None = None
    module: str | None = None
    cartridge: str | None = None
    entity_label: str | None = None
    contractor_count: int | None = None
    risk_factor: float | int | None = None
    value: PublicScalar = None
    count: int | None = None
    confidence: float | int | None = None
    priority_score: float | int | None = None
    impact_estimate: float | int | None = None
    impact_currency: str | None = None
    decision_intelligence: PublicDecisionIntelligence = Field(
        default_factory=PublicDecisionIntelligence
    )
    omega: PublicOmega = Field(default_factory=PublicOmega)


class PublicAlert(PublicBusinessItem):
    item_id: str | None = None
    alert_type: str | None = None
    advisory: bool | None = None
    occurrence_count: int | None = None
    push_ready: bool | None = None
    message: str | None = None


class PublicDashboardMeta(PublicProjectionModel):
    generated_at: str | None = None
    refresh_interval_seconds: int | None = None
    live_mode: str | None = None
    source_count: int = 0
    item_count: int = 0
    version: str | None = None
    app_env: str | None = None
    execution_mode: str | None = None
    supervised_execution_enabled: bool | None = None
    external_writeback_enabled: bool | None = None
    write_back_enabled: bool | None = None


class PublicDashboardSummary(PublicProjectionModel):
    total_items: int = 0
    total_anomalies: int = 0
    control_items: int = 0
    critical: int = 0
    attention: int = 0
    open_decisions: int = 0
    active_connectors: int = 0
    active_modules: int = 0
    active_cartridges: int = 0
    operational_cartridges: int = 0
    data_ready_sources: int = 0
    data_ready_modules: int = 0
    partial_modules: int = 0
    stub_modules: int = 0
    by_severity: PublicStatusCounts = Field(default_factory=PublicStatusCounts)
    source_states: PublicStatusCounts = Field(default_factory=PublicStatusCounts)
    data_readiness: PublicStatusCounts = Field(default_factory=PublicStatusCounts)


class PublicDashboardModule(PublicProjectionModel):
    id: str | None = None
    label: str | None = None
    domain: str | None = None
    accent: str | None = None
    description: str | None = None
    status: str | None = None
    active: bool | None = None
    operational: bool | None = None
    item_count: int = 0
    critical_count: int = 0
    cartridge_count: int = 0
    source_status: str | None = None
    data_readiness: str | None = None
    operationally_ready: bool | None = None
    modules: list[PublicDashboardModule] = Field(default_factory=list)


class PublicOmegaStep(PublicProjectionModel):
    id: str | None = None
    label: str | None = None


class ControlRoomLegacyDashboardResponse(PublicProjectionModel):
    meta: PublicDashboardMeta = Field(default_factory=PublicDashboardMeta)
    period: str | None = None
    omega_steps: list[PublicOmegaStep] = Field(default_factory=list)
    summary: PublicDashboardSummary = Field(default_factory=PublicDashboardSummary)
    domains: list[PublicDashboardModule] = Field(default_factory=list)
    cartridges: list[PublicDashboardModule] = Field(default_factory=list)
    sources: list[PublicSource] = Field(default_factory=list)
    alerts: list[PublicAlert] = Field(default_factory=list)
    items: list[PublicBusinessItem] = Field(default_factory=list)


class ControlRoomBusinessSummaryResponse(PublicProjectionModel):
    total_anomalies: int = 0
    by_severity: PublicStatusCounts = Field(default_factory=PublicStatusCounts)
    open_decisions: int = 0
    sources: list[PublicSource] = Field(default_factory=list)
    alerts: PublicStatusCounts = Field(default_factory=PublicStatusCounts)


class ControlRoomGoldWidget(PublicSlugIdentity):
    title: str | None = None
    value: PublicScalar = None
    contractor_count: int | None = None
    risk_factor: float | int | None = None
    status: str | None = None
    rows: list[PublicMetricRow] = Field(default_factory=list)


class ControlRoomGoldKpisResponse(PublicProjectionModel):
    generated_at: str | None = None
    widgets: list[ControlRoomGoldWidget] = Field(default_factory=list)


class ControlRoomLegacyAlertsResponse(PublicProjectionModel):
    alerts: list[PublicAlert] = Field(default_factory=list)
    summary: PublicStatusCounts = Field(default_factory=PublicStatusCounts)
    generated_at: str | None = None


class ControlRoomLegacyAnomaliesResponse(PublicProjectionModel):
    anomalies: list[PublicBusinessItem] = Field(default_factory=list)
    sources: list[PublicSource] = Field(default_factory=list)


class ControlRoomLegacyItemResponse(PublicBusinessItem):
    pass


__all__ = tuple(name for name in globals() if name.startswith("ControlRoom"))
