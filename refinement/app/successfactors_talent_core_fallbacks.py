from __future__ import annotations

TALENT_CORE_FALLBACK_SQL: dict[str, str] = {
    "sap_successfactors_talent_employee_profile": """
WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE COALESCE(is_active, FALSE) = TRUE
),
performance AS (
    SELECT
        user_id_hash,
        MAX(
            talent_score_scale(performance_rating) * 20
        ) AS performance_score,
        BOOL_OR(
            performance_status = 'ready'
            AND talent_score_is_valid(performance_rating)
        ) AS has_performance,
        BOOL_OR(
            performance_rating IS NOT NULL
            AND NOT talent_score_is_valid(performance_rating)
        ) AS saw_invalid_performance
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performance_cycle/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    GROUP BY user_id_hash
)
SELECT
    emp.tenant_id,
    emp.workspace_id,
    emp.user_id,
    emp.full_name,
    emp.company_id,
    emp.company_name,
    emp.division_id,
    emp.division_name,
    emp.department_id,
    emp.department_name,
    emp.location_id,
    emp.location_name,
    emp.job_code,
    emp.manager_id,
    0::BIGINT AS direct_reports,
    0::BIGINT AS hierarchy_depth,
    emp.start_date,
    emp.end_date,
    CASE
        WHEN TRY_CAST(emp.start_date AS DATE) IS NULL THEN NULL
        ELSE DATE_DIFF('month', TRY_CAST(emp.start_date AS DATE), CURRENT_DATE)
    END AS tenure_months,
    NULL::DOUBLE AS competency_score,
    performance.performance_score AS performance_score,
    NULL::DOUBLE AS aspiration_score,
    (COALESCE(performance.saw_invalid_performance, FALSE)
     AND NOT COALESCE(performance.has_performance, FALSE))
        AS invalid_performance_input,
    FALSE AS invalid_competency_input,
    FALSE AS invalid_aspiration_input,
    (COALESCE(performance.saw_invalid_performance, FALSE)
     AND NOT COALESCE(performance.has_performance, FALSE)) AS invalid_score_input,
    CASE
        WHEN COALESCE(performance.saw_invalid_performance, FALSE)
             AND NOT COALESCE(performance.has_performance, FALSE) THEN 'blocked'
        ELSE 'insufficient_data'
    END AS cpa_status,
    CASE
        WHEN COALESCE(performance.saw_invalid_performance, FALSE)
             AND NOT COALESCE(performance.has_performance, FALSE) THEN 'blocked'
        WHEN COALESCE(performance.has_performance, FALSE) THEN 'partial'
        ELSE 'foundation_ready'
    END AS profile_status,
    CASE
        WHEN COALESCE(performance.has_performance, FALSE)
            THEN '["missing_competency","missing_aspiration"]'
        ELSE '["missing_competency","missing_performance","missing_aspiration"]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN performance ON performance.user_id_hash = emp.user_id_hash
ORDER BY emp.user_id
""",
    "sap_successfactors_performance_cycle": """
WITH reviews AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performancereview_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
ranked AS (
    SELECT
        reviews.*,
        ROW_NUMBER() OVER (
            PARTITION BY user_id
            -- Ultima form CALIFICADA por usuario (no la mas reciente sin rating).
            ORDER BY (performance_rating IS NOT NULL) DESC, cycle_end_date DESC NULLS LAST, cycle_start_date DESC NULLS LAST, form_data_id DESC NULLS LAST
        ) AS _rn
    FROM reviews
    WHERE user_id IS NOT NULL
)
SELECT
    user_id,
    -- user_id ya es el formSubjectId shadowed (sha256); se expone como
    -- user_id_hash para unir con employee_360.user_id_hash sin des-shadowear.
    user_id AS user_id_hash,
    form_data_id,
    form_template_id,
    status AS review_status,
    performance_rating,
    potential_rating,
    cycle_start_date,
    cycle_end_date,
    0::BIGINT AS goals_total,
    0::BIGINT AS goals_completed,
    NULL::DOUBLE AS goals_percent_complete_avg,
    CASE WHEN performance_rating IS NULL THEN 'insufficient_data' ELSE 'ready' END AS performance_status,
    load_date
FROM ranked
WHERE _rn = 1
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
),
normalized AS (
    SELECT emp.*,
        (
            invalid_score_input IS DISTINCT FROM FALSE
            OR (competency_score IS NOT NULL
                AND NOT talent_percent_is_valid(competency_score))
            OR (performance_score IS NOT NULL
                AND NOT talent_percent_is_valid(performance_score))
            OR (aspiration_score IS NOT NULL
                AND NOT talent_percent_is_valid(aspiration_score))
        ) AS invalid_score_detected
    FROM emp
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
    CASE WHEN talent_percent_is_valid(competency_score)
        THEN TRY_CAST(competency_score AS DOUBLE) END AS competency_score,
    CASE WHEN talent_percent_is_valid(performance_score)
        THEN TRY_CAST(performance_score AS DOUBLE) END AS performance_score,
    CASE WHEN talent_percent_is_valid(aspiration_score)
        THEN TRY_CAST(aspiration_score AS DOUBLE) END AS aspiration_score,
    CASE WHEN talent_percent_is_valid(competency_score)
        THEN TRY_CAST(competency_score AS DOUBLE) END AS competency_100,
    CASE WHEN talent_percent_is_valid(performance_score)
        THEN TRY_CAST(performance_score AS DOUBLE) END AS performance_100,
    CASE WHEN talent_percent_is_valid(aspiration_score)
        THEN TRY_CAST(aspiration_score AS DOUBLE) END AS aspiration_100,
    invalid_score_detected AS invalid_score_input,
    NULL::DOUBLE AS fit_score,
    CASE WHEN invalid_score_detected THEN 'blocked'
         ELSE 'insufficient_data' END AS cpa_status,
    'partial' AS role_profile_status,
    'blocked' AS required_skills_status,
    -- GATE 3 (Fase B): blockers condicionales por componente (aqui C/P/A_100 son NULL
    -- por fallback foundation-safe, asi que emite los 3; mismo patron que cpa_scores).
    CASE WHEN invalid_score_detected THEN '["invalid_score_input"]' ELSE
    to_json(list_filter([
        CASE WHEN competency_100 IS NULL THEN 'KB-COMPETENCIAS blocked' END,
        CASE WHEN performance_100 IS NULL THEN 'KB-DESEMPENO blocked' END,
        CASE WHEN aspiration_100 IS NULL THEN 'KB-ASPIRACION blocked' END
    ], x -> x IS NOT NULL))::VARCHAR END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM normalized
ORDER BY user_id
""",
}
