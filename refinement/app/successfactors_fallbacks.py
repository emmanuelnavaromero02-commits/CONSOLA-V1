from __future__ import annotations

from typing import Any


_MISSING_DEPENDENCY_MARKERS = (
    "no files found",
    "source_files_missing",
    "dependency_not_materialized",
    "missing_materialized_dependencies",
    "404 (not found)",
    "http get error",
)


FOUNDATION_GOLD_FALLBACK_SQL: dict[str, str] = {
    "sap_successfactors_employee_360": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS gender,
    NULL::VARCHAR AS marital_status,
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS division_id,
    NULL::VARCHAR AS division_name,
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS job_code,
    NULL::VARCHAR AS cost_center,
    NULL::VARCHAR AS manager_id,
    NULL::DATE AS start_date,
    NULL::DATE AS end_date,
    FALSE::BOOLEAN AS is_active
WHERE FALSE
""",
    "sap_successfactors_org_structure": """
SELECT
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS division_id,
    NULL::VARCHAR AS division_name,
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS business_unit_id,
    NULL::VARCHAR AS business_unit_name
WHERE FALSE
""",
    "sap_successfactors_headcount_by_location": """
SELECT
    NULL::VARCHAR AS location_id,
    NULL::VARCHAR AS location_name,
    0::BIGINT AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
WHERE FALSE
""",
    "sap_successfactors_headcount_by_department": """
SELECT
    NULL::VARCHAR AS department_id,
    NULL::VARCHAR AS department_name,
    0::BIGINT AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
WHERE FALSE
""",
    "sap_successfactors_headcount_by_company": """
SELECT
    NULL::VARCHAR AS company_id,
    NULL::VARCHAR AS company_name,
    0::BIGINT AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
WHERE FALSE
""",
    "sap_successfactors_manager_hierarchy": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS manager_id,
    0::BIGINT AS direct_reports,
    0::BIGINT AS depth
WHERE FALSE
""",
}


TALENT_GOLD_FALLBACK_SQL: dict[str, str] = {
    "sap_successfactors_talent_employee_profile": """
WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE COALESCE(is_active, FALSE) = TRUE
)
SELECT
    tenant_id,
    workspace_id,
    user_id,
    full_name,
    company_id,
    company_name,
    division_id,
    division_name,
    department_id,
    department_name,
    location_id,
    location_name,
    job_code,
    manager_id,
    0::BIGINT AS direct_reports,
    0::BIGINT AS hierarchy_depth,
    start_date,
    end_date,
    CASE
        WHEN TRY_CAST(start_date AS DATE) IS NULL THEN NULL
        ELSE DATE_DIFF('month', TRY_CAST(start_date AS DATE), CURRENT_DATE)
    END AS tenure_months,
    NULL::DOUBLE AS competency_score,
    NULL::DOUBLE AS performance_score,
    NULL::DOUBLE AS aspiration_score,
    'insufficient_data' AS cpa_status,
    'foundation_ready' AS profile_status,
    '["missing_competency","missing_performance","missing_aspiration"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
ORDER BY user_id
""",
    "sap_successfactors_talent_role_profile": """
WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE job_code IS NOT NULL
),
rollup AS (
    SELECT
        tenant_id,
        workspace_id,
        job_code,
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE COALESCE(is_active, FALSE) = TRUE) AS active_employee_count,
        COUNT(DISTINCT department_id) AS departments_count,
        COUNT(DISTINCT location_id) AS locations_count,
        COUNT(DISTINCT company_id) AS companies_count
    FROM emp
    GROUP BY tenant_id, workspace_id, job_code
)
SELECT
    tenant_id,
    workspace_id,
    job_code,
    job_code AS role_name,
    employee_count,
    active_employee_count,
    departments_count,
    locations_count,
    companies_count,
    'blocked' AS required_skills_status,
    'partial' AS role_profile_status,
    '["Position requirements pending","Skills/competencies metadata pending"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM rollup
ORDER BY active_employee_count DESC, job_code
""",
    "sap_successfactors_talent_cpa_scores": """
WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    tenant_id,
    workspace_id,
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    direct_reports,
    tenure_months,
    COALESCE(job_code, '(sin rol)') AS role_name,
    TRY_CAST(competency_score AS DOUBLE) AS competency_score,
    TRY_CAST(performance_score AS DOUBLE) AS performance_score,
    TRY_CAST(aspiration_score AS DOUBLE) AS aspiration_score,
    NULL::DOUBLE AS competency_100,
    NULL::DOUBLE AS performance_100,
    NULL::DOUBLE AS aspiration_100,
    NULL::DOUBLE AS fit_score,
    'insufficient_data' AS cpa_status,
    'partial' AS role_profile_status,
    'blocked' AS required_skills_status,
    '["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
ORDER BY user_id
""",
    "sap_successfactors_talent_readiness": """
WITH cpa AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_cpa_scores/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    direct_reports,
    tenure_months,
    role_name,
    competency_score,
    performance_score,
    aspiration_score,
    fit_score,
    NULL::DOUBLE AS benchmark_score,
    ROUND(TRY_CAST(fit_score AS DOUBLE), 2) AS readiness_score,
    CASE WHEN TRY_CAST(fit_score AS DOUBLE) IS NULL THEN 'insufficient_data' ELSE 'cpa_real' END AS source_mode,
    NULL::VARCHAR AS benchmark_version,
    CASE
        WHEN TRY_CAST(fit_score AS DOUBLE) IS NULL THEN 'insufficient_data'
        WHEN TRY_CAST(fit_score AS DOUBLE) >= 80 THEN 'ready'
        WHEN TRY_CAST(fit_score AS DOUBLE) >= 60 THEN 'near'
        ELSE 'not_ready'
    END AS readiness_status,
    CASE
        WHEN TRY_CAST(fit_score AS DOUBLE) IS NULL THEN 'Datos insuficientes'
        WHEN TRY_CAST(fit_score AS DOUBLE) >= 80 THEN 'Ready'
        WHEN TRY_CAST(fit_score AS DOUBLE) >= 60 THEN 'Near'
        ELSE 'Not ready'
    END AS readiness_label,
    role_profile_status,
    required_skills_status,
    CASE WHEN TRY_CAST(fit_score AS DOUBLE) IS NULL THEN 3 ELSE 0 END AS blocker_count,
    CASE
        WHEN TRY_CAST(fit_score AS DOUBLE) IS NULL
            THEN '["talent_cpa_inputs_missing","benchmark_internal_not_configured"]'
        ELSE '[]'
    END AS blockers,
    'talent_readiness.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM cpa
ORDER BY user_id
""",
    "sap_successfactors_talent_9box": """
WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
scored AS (
    SELECT
        *,
        CASE
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NOT NULL AND TRY_CAST(performance_score AS DOUBLE) > 5
                THEN TRY_CAST(performance_score AS DOUBLE) / 20
            WHEN TRY_CAST(performance_score AS DOUBLE) IS NOT NULL
                THEN TRY_CAST(performance_score AS DOUBLE)
            WHEN TRY_CAST(readiness_score AS DOUBLE) IS NOT NULL
                THEN TRY_CAST(readiness_score AS DOUBLE) / 20
            ELSE NULL
        END AS performance_scale,
        CASE
            WHEN TRY_CAST(readiness_score AS DOUBLE) IS NOT NULL
                THEN TRY_CAST(readiness_score AS DOUBLE) / 20
            ELSE NULL
        END AS potential_scale
    FROM readiness
),
banded AS (
    SELECT
        *,
        CASE
            WHEN performance_scale IS NULL THEN 'insufficient_data'
            WHEN performance_scale >= 4 THEN 'high'
            WHEN performance_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS performance_band_calc,
        CASE
            WHEN potential_scale IS NULL THEN 'insufficient_data'
            WHEN potential_scale >= 4 THEN 'high'
            WHEN potential_scale >= 3 THEN 'medium'
            ELSE 'low'
        END AS potential_band_calc
    FROM scored
)
SELECT
    user_id,
    full_name,
    company_name,
    department_name,
    location_name,
    job_code,
    role_name,
    performance_score,
    ROUND(potential_scale, 2) AS potential_score,
    ROUND(readiness_score, 2) AS readiness_score,
    source_mode,
    benchmark_version,
    performance_band_calc AS performance_band,
    potential_band_calc AS potential_band,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'insufficient_data'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'low' THEN 'enigma'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'medium' THEN 'crecimiento'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'high' THEN 'estrella'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'low' THEN 'dilema'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'medium' THEN 'core'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'high' THEN 'alto_impacto'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'low' THEN 'riesgo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'medium' THEN 'efectivo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'high' THEN 'experto'
        ELSE 'insufficient_data'
    END AS box_key,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'Sin datos suficientes'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'low' THEN 'Enigma'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'medium' THEN 'Crecimiento'
        WHEN potential_band_calc = 'high' AND performance_band_calc = 'high' THEN 'Estrella'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'low' THEN 'Dilema'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'medium' THEN 'Core'
        WHEN potential_band_calc = 'medium' AND performance_band_calc = 'high' THEN 'Alto Impacto'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'low' THEN 'Riesgo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'medium' THEN 'Efectivo'
        WHEN potential_band_calc = 'low' AND performance_band_calc = 'high' THEN 'Experto'
        ELSE 'Sin datos suficientes'
    END AS box_label,
    CASE
        WHEN performance_band_calc = 'insufficient_data' OR potential_band_calc = 'insufficient_data' THEN 'blocked'
        WHEN source_mode = 'benchmark_internal' THEN 'benchmark_internal'
        ELSE 'ready'
    END AS box_status,
    blockers,
    'talent_9box.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
FROM banded
ORDER BY user_id
""",
    "sap_successfactors_talent_mobility_history": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS full_name,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS job_code,
    NULL::BOOLEAN AS is_active,
    NULL::DATE AS first_assignment_date,
    NULL::DATE AS latest_assignment_date,
    0::BIGINT AS movement_events,
    0::BIGINT AS distinct_departments,
    0::BIGINT AS distinct_locations,
    0::BIGINT AS distinct_job_codes,
    0::BIGINT AS distinct_managers,
    NULL::VARCHAR AS latest_event_reason,
    'insufficient_data' AS mobility_status,
    CURRENT_TIMESTAMP AS generated_at
WHERE FALSE
""",
    "sap_successfactors_talent_9box_operational": """
WITH boxes(box_key, box_label, potential_band, performance_band, movement_action, display_order) AS (
    VALUES
        ('enigma', 'Enigma', 'high', 'low', 'Cambio de rol o coaching de fit', 1),
        ('crecimiento', 'Crecimiento', 'high', 'medium', 'Asignacion de estiramiento y rotacion', 2),
        ('estrella', 'Estrella', 'high', 'high', 'Sucesion, promocion y retencion', 3),
        ('dilema', 'Dilema', 'medium', 'low', 'Plan de mejora o reubicacion', 4),
        ('core', 'Core', 'medium', 'medium', 'Retener y desarrollo continuo', 5),
        ('alto_impacto', 'Alto Impacto', 'medium', 'high', 'Promocion a siguiente nivel', 6),
        ('riesgo', 'Riesgo', 'low', 'low', 'PIP o gestion de salida', 7),
        ('efectivo', 'Efectivo', 'low', 'medium', 'Mantener en rol', 8),
        ('experto', 'Experto', 'low', 'high', 'Via tecnica y retencion en rol', 9)
)
SELECT
    box_key,
    box_label,
    potential_band,
    performance_band,
    movement_action,
    0::BIGINT AS employee_count,
    0::BIGINT AS ready_count,
    0::BIGINT AS benchmark_count,
    0::BIGINT AS blocked_count,
    'blocked' AS box_status,
    display_order,
    CURRENT_TIMESTAMP AS generated_at
FROM boxes
""",
    "sap_successfactors_talent_performance_goals": """
SELECT
    'missing' AS review_status,
    0::BIGINT AS employees_evaluated,
    NULL::DOUBLE AS avg_performance_rating,
    NULL::DOUBLE AS avg_potential_rating,
    0::BIGINT AS goals_total,
    0::BIGINT AS goals_completed,
    NULL::DOUBLE AS goals_percent_complete_avg,
    'insufficient_data' AS performance_kpi_status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_competency_skill_gap": """
SELECT
    '(sin rol)' AS job_code,
    '(sin rol)' AS role_name,
    0::BIGINT AS employees_with_skills,
    0::BIGINT AS distinct_skills,
    NULL::DOUBLE AS avg_proficiency_100,
    'blocked' AS required_skills_status,
    'blocked' AS skill_gap_status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_aspiration_signals": """
SELECT
    'missing' AS source_entity,
    0::BIGINT AS employees_with_aspiration,
    0::BIGINT AS target_roles,
    NULL::DOUBLE AS avg_aspiration_100,
    0::BIGINT AS mobility_preferences,
    'insufficient_data' AS aspiration_signal_status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_role_coverage": """
SELECT
    0::BIGINT AS job_codes_with_employees,
    0::BIGINT AS roles_or_positions_defined,
    0::BIGINT AS active_employees_covered,
    0::BIGINT AS roles_with_requirement_signal,
    0::BIGINT AS roles_blocked,
    'blocked' AS role_coverage_status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_learning_certification_status": """
SELECT
    'missing' AS learning_status,
    0::BIGINT AS learning_events,
    0::BIGINT AS employees,
    0::BIGINT AS completed_events,
    0::BIGINT AS overdue_events,
    0::DOUBLE AS credit_hours,
    'insufficient_data' AS learning_kpi_status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_recruitment_application_funnel": """
SELECT
    '(sin departamento)' AS department,
    '(sin etapa)' AS application_status,
    '(sin fuente)' AS source,
    0::BIGINT AS requisitions,
    0::BIGINT AS applications,
    0::BIGINT AS candidates,
    'insufficient_data' AS application_funnel_status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_retention_risk": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS job_code,
    NULL::VARCHAR AS role_name,
    'insufficient_data' AS readiness_status,
    NULL::DOUBLE AS fit_score,
    0::BIGINT AS movement_events,
    NULL::BIGINT AS months_since_movement,
    NULL::DOUBLE AS retention_risk_score,
    'missing' AS risk_band,
    'blocked' AS status,
    '["talent_retention_inputs_missing"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
WHERE FALSE
""",
    "sap_successfactors_talent_promotion_alignment": """
SELECT
    'summary' AS box_key,
    'Promociones vs calibracion' AS box_label,
    0::BIGINT AS promotion_count,
    0::BIGINT AS aligned_count,
    0::BIGINT AS misaligned_count,
    'partial' AS status,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_calibration_sensitivity": """
SELECT
    0::BIGINT AS employee_count,
    0::BIGINT AS classified_count,
    0::BIGINT AS near_cut_count,
    NULL::DOUBLE AS near_cut_pct,
    'blocked' AS status,
    '["talent_9box_inputs_missing"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
""",
    "sap_successfactors_talent_role_fit_assignments": """
SELECT
    NULL::VARCHAR AS user_id,
    NULL::VARCHAR AS company_name,
    NULL::VARCHAR AS department_name,
    NULL::VARCHAR AS location_name,
    NULL::VARCHAR AS current_job_code,
    NULL::VARCHAR AS current_role_name,
    NULL::DOUBLE AS fit_score,
    'insufficient_data' AS readiness_status,
    'blocked' AS required_skills_status,
    NULL::VARCHAR AS assignment_recommendation,
    'blocked' AS status,
    '["talent_role_fit_inputs_missing"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
WHERE FALSE
""",
    "sap_successfactors_talent_action_candidates": """
SELECT
    NULL::VARCHAR AS action_id,
    NULL::VARCHAR AS action_type,
    NULL::VARCHAR AS severity,
    0::BIGINT AS affected_count,
    NULL::VARCHAR AS title,
    NULL::VARCHAR AS recommendation,
    'blocked' AS status,
    'fallback' AS method,
    CURRENT_TIMESTAMP AS generated_at
WHERE FALSE
""",
    "sap_successfactors_talent_signals": """
SELECT
    NULL::VARCHAR AS signal_id,
    NULL::VARCHAR AS signal_type,
    NULL::VARCHAR AS severity,
    0::BIGINT AS affected_count,
    NULL::VARCHAR AS title,
    NULL::VARCHAR AS recommendation,
    'blocked' AS status,
    CURRENT_TIMESTAMP AS generated_at,
    'blocked' AS readiness_status,
    0::BIGINT AS source_row_count,
    CURRENT_TIMESTAMP AS materialized_at,
    '["talent_signals_inputs_missing"]' AS blockers
WHERE FALSE
""",
    "sap_successfactors_talent_operational_features": """
SELECT
    'WB-TALENTO' AS source_id,
    NULL::VARCHAR AS workspace_id,
    CURRENT_TIMESTAMP AS materialized_at,
    0::BIGINT AS employee_count,
    0::BIGINT AS profiled_count,
    0::BIGINT AS profiled_employee_count,
    0::BIGINT AS calculable_count,
    0::BIGINT AS calculable_employee_count,
    0::BIGINT AS readiness_low,
    0::BIGINT AS readiness_low_count,
    0::BIGINT AS readiness_medium,
    0::BIGINT AS readiness_medium_count,
    0::BIGINT AS readiness_high,
    0::BIGINT AS readiness_high_count,
    0::BIGINT AS readiness_pending_count,
    0::BIGINT AS readiness_cpa_real_count,
    0::BIGINT AS readiness_benchmark_count,
    0::BIGINT AS nine_box_classified_count,
    0::BIGINT AS nine_box_blocked_count,
    0::BIGINT AS nine_box_pending_count,
    0::BIGINT AS nine_box_operational_ready_count,
    0::BIGINT AS role_count,
    0::BIGINT AS roles_without_requirements,
    0::BIGINT AS roles_without_requirements_count,
    0::BIGINT AS open_signal_count,
    0::BIGINT AS high_severity_signal_count,
    '{"critical":0,"high":0,"medium":0,"low":0}' AS open_signals_by_severity,
    1::BIGINT AS learning_blocked_count,
    1::BIGINT AS recruiting_blocked_count,
    0::BIGINT AS mobility_observed_count,
    1::BIGINT AS skill_gap_count,
    0::DOUBLE AS skill_coverage_pct,
    0::DOUBLE AS role_requirements_coverage_pct,
    0::DOUBLE AS nine_box_coverage_pct,
    0::DOUBLE AS confidence,
    'insufficient_data' AS source_mode,
    'blocked' AS readiness_status,
    'blocked' AS feature_status,
    'En espera de datos' AS user_status_label,
    '["feature_pack_inputs_missing","benchmark_internal_not_configured"]' AS blockers,
    'talent_operational_features.v2' AS contract_version,
    CURRENT_TIMESTAMP AS generated_at
""",
}

SUCCESSFACTORS_GOLD_FALLBACK_SOURCES: dict[str, list[str]] = {
    "sap_successfactors_talent_employee_profile": [
        "gold/sap_successfactors/sap_successfactors_employee_360",
    ],
    "sap_successfactors_talent_role_profile": [
        "gold/sap_successfactors/sap_successfactors_employee_360",
    ],
    "sap_successfactors_talent_cpa_scores": [
        "gold/sap_successfactors/sap_successfactors_talent_employee_profile",
    ],
    "sap_successfactors_talent_readiness": [
        "gold/sap_successfactors/sap_successfactors_talent_cpa_scores",
    ],
    "sap_successfactors_talent_9box": [
        "gold/sap_successfactors/sap_successfactors_talent_readiness",
    ],
}

SUCCESSFACTORS_GOLD_FALLBACK_SQL: dict[str, str] = {
    **FOUNDATION_GOLD_FALLBACK_SQL,
    **TALENT_GOLD_FALLBACK_SQL,
}


def is_missing_successfactors_dependency_error(exc: Exception | Any) -> bool:
    text = " ".join(str(part) for part in getattr(exc, "args", ()) or (str(exc),)).lower()
    return any(marker in text for marker in _MISSING_DEPENDENCY_MARKERS)


def fallback_dataset_for_successfactors(ds: dict[str, Any], exc: Exception | Any) -> dict[str, Any] | None:
    name = str(ds.get("name") or "")
    sql = SUCCESSFACTORS_GOLD_FALLBACK_SQL.get(name)
    if not sql or not is_missing_successfactors_dependency_error(exc):
        return None
    return {
        **ds,
        "sql_def": sql.strip(),
        "sources": SUCCESSFACTORS_GOLD_FALLBACK_SOURCES.get(name, []),
        "description": (
            str(ds.get("description") or "").strip()
            + " Fallback operativo: dependencia SuccessFactors no materializada."
        ).strip(),
    }
