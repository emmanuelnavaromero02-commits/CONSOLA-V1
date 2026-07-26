from __future__ import annotations

from pydantic import Field

from app.schemas.control_room_public_projection import PublicProjectionModel


class PublicReadinessSeries(PublicProjectionModel):
    ticker: str | None = None
    metric_name: str | None = None
    as_of: str | None = None
    unit: str | None = None
    value: float | int | None = None
    confidence: float | int | None = None
    status: str | None = None
    usable: bool | None = None


class ControlRoomReadinessResponse(PublicProjectionModel):
    status: str = ""
    series_count: int = 0
    usable_count: int = 0
    series: list[PublicReadinessSeries] = Field(default_factory=list)


class MarketValidationSource(PublicProjectionModel):
    input_status: str | None = None
    source_mode: str | None = None
    employee_count: int = 0
    confidence: float | int | None = None


class MarketValidationContext(PublicProjectionModel):
    provider: str | None = None
    metric_name: str | None = None
    as_of: str | None = None
    unit: str | None = None
    confidence: float | int | None = None
    freshness_status: str | None = None


class MarketValidationSimulation(PublicProjectionModel):
    available: bool = False
    output_metric: str | None = None
    p10: float | int | None = None
    p50: float | int | None = None
    p90: float | int | None = None
    updated_at: str | None = None
    market_evidence_count: int = 0


class MarketValidationBayes(PublicProjectionModel):
    status: str | None = None
    sample_count: int = 0
    evidence_policy: str | None = None


class MarketValidationOrchestration(PublicProjectionModel):
    available: bool = False
    problem_type: str | None = None
    action_recommended: bool | None = None


class MarketValidationPolicy(PublicProjectionModel):
    recommendation_only: bool | None = None
    causal_claim: bool | None = None
    financial_forecast: bool | None = None
    creates_calibration_observation: bool | None = None
    automatic_action: bool | None = None
    external_writeback: bool | None = None


class ControlRoomMarketValidationResponse(PublicProjectionModel):
    status: str = ""
    source: MarketValidationSource = Field(default_factory=MarketValidationSource)
    market_context: MarketValidationContext = Field(
        default_factory=MarketValidationContext
    )
    simulation: MarketValidationSimulation = Field(
        default_factory=MarketValidationSimulation
    )
    bayes: MarketValidationBayes = Field(default_factory=MarketValidationBayes)
    orchestration: MarketValidationOrchestration = Field(
        default_factory=MarketValidationOrchestration
    )
    policy: MarketValidationPolicy = Field(default_factory=MarketValidationPolicy)


class PublicThreshold(PublicProjectionModel):
    anomaly_type: str | None = None
    metric: str | None = None
    warning_value: float | int | None = None
    critical_value: float | int | None = None
    currency: str | None = None
    enabled: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ThresholdSummary(PublicProjectionModel):
    total: int = 0
    active: int = 0
    disabled: int = 0
    recent: list[PublicThreshold] = Field(default_factory=list)


class ControlRoomThresholdsResponse(PublicProjectionModel):
    thresholds: list[PublicThreshold] = Field(default_factory=list)
    summary: ThresholdSummary = Field(default_factory=ThresholdSummary)


class PublicLesson(PublicProjectionModel):
    anomaly_type: str | None = None
    rule: str | None = None
    confidence: float | int | None = None
    created_at: str | None = None


class LessonPattern(PublicProjectionModel):
    anomaly_type: str | None = None
    count: int = 0
    latest_rule: str | None = None
    last_seen_at: str | None = None
    avg_confidence: float | int | None = None


class LessonSummary(PublicProjectionModel):
    total: int = 0
    recent: list[PublicLesson] = Field(default_factory=list)
    top_patterns: list[LessonPattern] = Field(default_factory=list)


class ControlRoomLessonsResponse(PublicProjectionModel):
    lessons: list[PublicLesson] = Field(default_factory=list)
    summary: LessonSummary = Field(default_factory=LessonSummary)


__all__ = tuple(name for name in globals() if name.startswith("ControlRoom"))
