from __future__ import annotations


def _unavailable_projection(name: str) -> str:
    if name.endswith("talent_readiness"):
        return """
-- Disabled legacy algorithm: sap_successfactors_talent_benchmark_internal,
-- PERCENT_RANK(), workspace_employee_count >= 50, benchmark_input_coverage >= 0.80.
SELECT
    NULL::VARCHAR AS tenant_id, NULL::VARCHAR AS workspace_id,
    NULL::VARCHAR AS user_id, NULL::DOUBLE AS benchmark_raw_score,
    NULL::DOUBLE AS benchmark_score, NULL::DOUBLE AS readiness_score,
    'insufficient_data' AS source_mode,
    FALSE AS benchmark_approval_valid,
    'unreviewed' AS benchmark_provenance_status,
    'insufficient_data' AS readiness_status,
    1::BIGINT AS blocker_count,
    '["missing_materialized_dependencies"]' AS blockers,
    NULL::DOUBLE AS confidence
WHERE FALSE
"""
    return """
-- benchmark_performance_percentile and benchmark_potential_percentile are unavailable.
SELECT
    NULL::VARCHAR AS tenant_id, NULL::VARCHAR AS workspace_id,
    NULL::VARCHAR AS user_id, NULL::DOUBLE AS performance_score,
    NULL::DOUBLE AS potential_score, NULL::DOUBLE AS performance_proxy_score,
    NULL::DOUBLE AS potential_proxy_score,
    NULL::DOUBLE AS benchmark_performance_proxy,
    NULL::DOUBLE AS benchmark_potential_proxy,
    'insufficient_data' AS source_mode,
    FALSE AS benchmark_approval_valid,
    'unreviewed' AS benchmark_provenance_status,
    NULL::VARCHAR AS performance_band, NULL::VARCHAR AS potential_band,
    'insufficient_data' AS box_key, 'blocked' AS box_status,
    '["missing_materialized_dependencies"]' AS blockers
WHERE FALSE
"""


def _runtime_sql(name: str) -> str:
    return _unavailable_projection(name)


TALENT_RUNTIME_FALLBACK_SQL = {
    name: _runtime_sql(name)
    for name in (
        "sap_successfactors_talent_readiness",
        "sap_successfactors_talent_9box",
    )
}
