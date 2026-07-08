"""Unit tests for the generic Gold-signal fallback guardrails.

Regression coverage for the two production failure modes fixed in Phase 1 (B-i):
  1. Cross-sectional Gold snapshots (one row per employee, a single snapshot)
     were fabricated into a time series -> spurious anomalies.
  2. Identifier / structural columns (user_id, *_id, display_order,
     source_row_count) won the "widest numeric spread" ranking and were treated
     as business KPIs (e.g. the generic_user_id garbage seen in SF talent gold).
"""

from __future__ import annotations

from app.services.intelligence.baseline import build_metric_artifacts
from app.services.intelligence.gold_control_room import (
    build_generic_gold_artifacts,
    infer_generic_metric,
)


def _cross_sectional_talent_rows() -> list[dict]:
    # One row per employee, a single snapshot (constant generated_at, no period).
    return [
        {
            "generated_at": "2026-07-05 04:43:23",
            "tenant_id": "T",
            "workspace_id": "W",
            "user_id": 103169,
            "full_name": "x",
            "performance_100": 80,
        },
        {
            "generated_at": "2026-07-05 04:43:23",
            "tenant_id": "T",
            "workspace_id": "W",
            "user_id": 103252,
            "full_name": "y",
            "performance_100": 40,
        },
        {
            "generated_at": "2026-07-05 04:43:23",
            "tenant_id": "T",
            "workspace_id": "W",
            "user_id": 999999999,  # sentinel: previously won by spread -> generic_user_id
            "full_name": "z",
            "performance_100": 10,
        },
    ]


def test_generic_metric_skips_cross_sectional_snapshot():
    rows = _cross_sectional_talent_rows()
    assert infer_generic_metric(dataset="sap_successfactors_talent_cpa_scores", rows=rows) is None


def test_build_generic_reports_cross_sectional_status_and_no_artifacts():
    rows = _cross_sectional_talent_rows()
    artifacts, skipped = build_generic_gold_artifacts(
        cartridge_id="sap_successfactors",
        dataset="sap_successfactors_talent_cpa_scores",
        rows=rows,
        build_metric_artifacts=build_metric_artifacts,
    )
    assert artifacts == []
    assert len(skipped) == 1
    assert skipped[0]["status"] == "cross_sectional_no_timeseries"
    assert skipped[0]["control_origin"] == "generic_gold_signal"


def test_generic_metric_never_selects_identifier_as_value():
    # Real monitoring period (>=2 points) + a huge-range identifier + a real KPI.
    rows = [
        {"snapshot_month": "2026-01-01", "org": "A", "user_id": 10230456, "score": 70},
        {"snapshot_month": "2026-02-01", "org": "A", "user_id": 88886666, "score": 72},
        {"snapshot_month": "2026-03-01", "org": "A", "user_id": 999999999, "score": 40},
    ]
    metric = infer_generic_metric(dataset="some_gold", rows=rows)
    assert metric is not None
    # user_id has the widest numeric spread but must be excluded as structural.
    assert metric["value_field"] == "score"
    assert metric["id"] == "generic_score"


def test_generic_entity_excludes_scoping_columns():
    rows = [
        {"snapshot_month": "2026-01-01", "tenant_id": "T", "workspace_id": "W", "department_name": "HR", "headcount": 10},
        {"snapshot_month": "2026-02-01", "tenant_id": "T", "workspace_id": "W", "department_name": "HR", "headcount": 14},
        {"snapshot_month": "2026-03-01", "tenant_id": "T", "workspace_id": "W", "department_name": "HR", "headcount": 22},
    ]
    metric = infer_generic_metric(dataset="headcount_by_department", rows=rows)
    assert metric is not None
    assert metric["value_field"] == "headcount"
    # entity must be the business label, never tenant_id / workspace_id.
    assert metric["entity"]["id_field"] == "department_name"


def test_generic_still_fires_on_real_timeseries():
    # Regression guard: legitimate uncontracted time series must still produce a signal.
    rows = [
        {"mes": "2026-01-01", "account": "A", "amount": 100},
        {"mes": "2026-02-01", "account": "A", "amount": 110},
        {"mes": "2026-03-01", "account": "A", "amount": 220},
    ]
    artifacts, _skipped = build_generic_gold_artifacts(
        cartridge_id="hubspot",
        dataset="new_gold_dataset",
        rows=rows,
        build_metric_artifacts=build_metric_artifacts,
    )
    assert len(artifacts) == 1
    signal = artifacts[0]["signal"]
    assert signal["control_origin"] == "generic_gold_signal"
    assert signal["metric"] == "generic_amount"


def test_generic_metric_requires_two_distinct_periods():
    # A monitoring-period column present but with a single distinct value is still
    # a single snapshot -> no series.
    rows = [
        {"snapshot_month": "2026-01-01", "org": "A", "score": 70},
        {"snapshot_month": "2026-01-01", "org": "B", "score": 40},
    ]
    assert infer_generic_metric(dataset="some_gold", rows=rows) is None


def test_generic_allows_index_kpi_as_value_not_identifier():
    # Regression guard (round-1/3 finding): *_index / *_rank are legitimate KPIs
    # and must remain eligible as the value axis; only hard identifiers are excluded.
    rows = [
        {"snapshot_month": "2026-01-01", "org": "A", "user_id": 999999999, "engagement_index": 71},
        {"snapshot_month": "2026-02-01", "org": "A", "user_id": 103252, "engagement_index": 68},
        {"snapshot_month": "2026-03-01", "org": "A", "user_id": 103169, "engagement_index": 55},
    ]
    metric = infer_generic_metric(dataset="talent_engagement", rows=rows)
    assert metric is not None
    assert metric["value_field"] == "engagement_index"
    assert metric["id"] == "generic_engagement_index"


def test_generic_detects_date_and_fecha_named_periods():
    # Regression guard: bilingual monitoring-period column names must be detected.
    for period_col in ("date", "fecha", "week", "quarter"):
        rows = [
            {period_col: "2026-01-01", "acct": "A", "amount": 100},
            {period_col: "2026-02-01", "acct": "A", "amount": 130},
            {period_col: "2026-03-01", "acct": "A", "amount": 90},
        ]
        metric = infer_generic_metric(dataset="fin_gold", rows=rows)
        assert metric is not None, f"expected series for period column {period_col!r}"
        assert metric["time_field"] == period_col
        assert metric["value_field"] == "amount"


def test_generic_rejects_attribute_date_as_period():
    # start_date is a per-row attribute (hire date), not a monitoring period:
    # a single snapshot must stay cross-sectional.
    rows = [
        {"start_date": "2017-01-01", "org": "A", "tenure_months": 30},
        {"start_date": "2019-06-01", "org": "A", "tenure_months": 12},
        {"start_date": "2020-03-01", "org": "B", "tenure_months": 8},
    ]
    assert infer_generic_metric(dataset="employees", rows=rows) is None


def test_generic_detects_period_boundary_names_despite_end_token():
    # month_end / quarter_end / period_end carry a strong period token; the "end"
    # token must not block them (regression from the earlier substring blocklist).
    for period_col in ("month_end", "quarter_end", "period_end"):
        rows = [
            {period_col: "2026-01-31", "org": "A", "revenue": 100},
            {period_col: "2026-02-28", "org": "A", "revenue": 130},
            {period_col: "2026-03-31", "org": "A", "revenue": 90},
        ]
        metric = infer_generic_metric(dataset="fin_gold", rows=rows)
        assert metric is not None, period_col
        assert metric["time_field"] == period_col


def test_generic_rejects_attribute_date_columns_default_deny():
    # <attr>_date columns whose descriptive token is not a period token must NOT
    # fabricate a series from a per-entity snapshot (default-deny).
    for attr_col in ("signup_date", "order_date", "hire_date", "close_date"):
        rows = [
            {attr_col: "2024-01-01", "user_id": 1, "engagement_index": 70},
            {attr_col: "2025-06-01", "user_id": 2, "engagement_index": 55},
            {attr_col: "2026-03-01", "user_id": 3, "engagement_index": 88},
        ]
        assert infer_generic_metric(dataset="snap", rows=rows) is None, attr_col


def test_generic_year_column_ignores_nonyear_codes():
    # A column named 'year' holding 4-digit facility codes (not 19xx/20xx) must
    # not be treated as a monitoring period.
    rows = [
        {"year": 1000, "org": "A", "score": 70},
        {"year": 3000, "org": "A", "score": 72},
        {"year": 5000, "org": "B", "score": 40},
    ]
    assert infer_generic_metric(dataset="fac", rows=rows) is None


def test_generic_detects_timestamp_axis():
    rows = [
        {"timestamp": "2026-01-01", "org": "A", "amount": 100},
        {"timestamp": "2026-02-01", "org": "A", "amount": 130},
        {"timestamp": "2026-03-01", "org": "A", "amount": 90},
    ]
    metric = infer_generic_metric(dataset="g", rows=rows)
    assert metric is not None
    assert metric["time_field"] == "timestamp"


def test_generic_period_name_token_matching_not_substring():
    # "calendar_month" contains the substring "end" but as a token it is
    # {"calendar","month"} -> must NOT be blocked as an attribute date.
    rows = [
        {"calendar_month": "2026-01-01", "org": "A", "amount": 100},
        {"calendar_month": "2026-02-01", "org": "A", "amount": 130},
        {"calendar_month": "2026-03-01", "org": "A", "amount": 90},
    ]
    metric = infer_generic_metric(dataset="gold", rows=rows)
    assert metric is not None
    assert metric["time_field"] == "calendar_month"


def test_generic_rejects_period_named_nondate_decoy():
    # notice_period matches the name heuristic but its values are day counts, not
    # dates -> value-aware detection must reject it as a time axis.
    rows = [
        {"notice_period": 30, "org": "A", "score": 70},
        {"notice_period": 60, "org": "A", "score": 72},
        {"notice_period": 90, "org": "B", "score": 40},
    ]
    assert infer_generic_metric(dataset="hr_gold", rows=rows) is None
