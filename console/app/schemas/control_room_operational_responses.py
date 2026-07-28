from __future__ import annotations

from pydantic import Field

from app.schemas.control_room_business_responses import PublicStatusCounts
from app.schemas.control_room_public_projection import PublicProjectionModel


class OperationalItems(PublicProjectionModel):
    total: int = 0
    by_status: PublicStatusCounts = Field(default_factory=PublicStatusCounts)


class OperationalExecutionCounts(PublicProjectionModel):
    pending: int = 0
    previewed: int = 0
    approved: int = 0
    rejected: int = 0
    executing: int = 0
    completed: int = 0
    failed: int = 0
    pending_reconciliation: int = 0
    ambiguous: int = 0


class ControlRoomOpsSummaryResponse(PublicProjectionModel):
    version: str | None = None
    app_env: str | None = None
    items: OperationalItems = Field(default_factory=OperationalItems)
    open_items_by_severity: PublicStatusCounts = Field(
        default_factory=PublicStatusCounts
    )
    action_executions: OperationalExecutionCounts = Field(
        default_factory=OperationalExecutionCounts
    )
    lessons: int = 0
    thresholds_active: int = 0
    last_item_seen_at: str | None = None
    execution_mode: str | None = None
    supervised_execution_enabled: bool | None = None
    external_writeback_enabled: bool | None = None
    write_back_enabled: bool | None = None
    writeback_blocked_by_default: bool | None = None
    external_writeback_blocked_by_default: bool | None = None
    has_demo_seed: bool | None = None


class AgentOpsRun(PublicProjectionModel):
    agent_name: str | None = None
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    tool_count: int = 0


class AgentOpsAlerts(PublicProjectionModel):
    total: int = 0
    open: int = 0
    last_seen_at: str | None = None


class AgentOpsAgent(PublicProjectionModel):
    name: str | None = None
    active: bool | None = None
    role: str | None = None
    monitor: bool | None = None
    operationally_ready: bool | None = None
    operational_tools_count: int = 0
    last_run: AgentOpsRun | None = None
    alerts: AgentOpsAlerts = Field(default_factory=AgentOpsAlerts)


class AgentOpsExecution(PublicProjectionModel):
    total: int = 0
    by_status: OperationalExecutionCounts = Field(
        default_factory=OperationalExecutionCounts
    )
    latest_at: str | None = None


class AgentOpsEngine(PublicProjectionModel):
    engine: str | None = None
    configured: int = 0
    evidence_count: int = 0
    sample_count: int = 0
    latest_at: str | None = None
    status: str | None = None
    executions: AgentOpsExecution | None = None


class AgentOpsOrigin(PublicProjectionModel):
    origin: str | None = None
    count: int = 0


class AgentOpsDiagnostic(PublicProjectionModel):
    diagnostic: str | None = None
    scope: str | None = None
    state_count: int = 0
    sample_count: int = 0
    latest_at: str | None = None
    included_in_business_counters: bool | None = None


class AgentOpsSummary(PublicProjectionModel):
    agents_total: int = 0
    active_agents: int = 0
    monitor_agents: int = 0
    recent_runs: int = 0
    failed_recent_runs: int = 0
    open_agent_alerts: int = 0
    agent_alerts_total: int = 0
    configured_engines: int = 0
    monte_carlo_simulations: int = 0
    bayesian_calibration_states: int = 0
    bayesian_calibration_samples: int = 0
    decision_orchestrations: int = 0


class ControlRoomAgentsOpsResponse(PublicProjectionModel):
    generated_at: str | None = None
    summary: AgentOpsSummary = Field(default_factory=AgentOpsSummary)
    agents: list[AgentOpsAgent] = Field(default_factory=list)
    recent_runs: list[AgentOpsRun] = Field(default_factory=list)
    engines: list[AgentOpsEngine] = Field(default_factory=list)
    origins: list[AgentOpsOrigin] = Field(default_factory=list)
    operational_diagnostics: list[AgentOpsDiagnostic] = Field(default_factory=list)


class DecisionIntelligenceRun(PublicProjectionModel):
    source_system: str | None = None
    run_mode: str | None = None
    status: str | None = None
    signals_generated: int = 0
    signals_skipped: int = 0
    dataset_unavailable_count: int = 0
    insufficient_history_count: int = 0
    app_version: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DecisionIntelligenceSnapshot(PublicProjectionModel):
    recommended_decision: str | None = None
    anomaly_probability: float | int | None = None
    uncertainty_level: str | None = None
    expected_impact_value: float | int | None = None
    expected_impact_currency: str | None = None
    data_quality_status: str | None = None
    method: str | None = None
    outcome_status: str | None = None
    measured_impact: float | int | None = None
    calibration_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ControlRoomDecisionIntelligenceRunsResponse(PublicProjectionModel):
    runs: list[DecisionIntelligenceRun] = Field(default_factory=list)


class ControlRoomDecisionIntelligenceRunDetailResponse(PublicProjectionModel):
    run: DecisionIntelligenceRun = Field(default_factory=DecisionIntelligenceRun)
    snapshots: list[DecisionIntelligenceSnapshot] = Field(default_factory=list)


class ControlRoomDecisionIntelligenceHistoryResponse(PublicProjectionModel):
    history: list[DecisionIntelligenceSnapshot] = Field(default_factory=list)


class CalibrationBucket(PublicProjectionModel):
    bucket: str | None = None
    sample_count: int = 0
    observed_success_rate: float | int | None = None


class CalibrationOutcomeSummary(PublicProjectionModel):
    status: str | None = None
    total_results: int = 0
    total_labeled: int = 0
    min_labels_required: int = 0
    insufficient_labeled_data: bool | None = None
    label_source: str | None = None
    rationale: str | None = None


class CalibrationNextRequiredData(PublicProjectionModel):
    outcomes_needed: int = 0
    periods_needed: int = 0
    metrics_needed: list[str] = Field(default_factory=list)
    labels_needed: int = 0


class ControlRoomDecisionIntelligenceCalibrationResponse(PublicProjectionModel):
    status: str = ""
    total_snapshots: int = 0
    total_with_outcome: int = 0
    insufficient_outcomes: bool | None = None
    min_outcomes_required: int = 0
    avg_expected_impact: float | int | None = None
    avg_measured_impact: float | int | None = None
    rationale: str | None = None
    outcome_linked_summary: CalibrationOutcomeSummary = Field(
        default_factory=CalibrationOutcomeSummary
    )
    insufficient_labeled_data: bool | None = None
    next_required_data: CalibrationNextRequiredData = Field(
        default_factory=CalibrationNextRequiredData
    )
    observed_success_rate: float | int | None = None
    calibration_buckets: list[CalibrationBucket] = Field(default_factory=list)


__all__ = tuple(name for name in globals() if name.startswith("ControlRoom"))
