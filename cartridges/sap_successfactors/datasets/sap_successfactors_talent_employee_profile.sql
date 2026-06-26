-- sap_successfactors_talent_employee_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy"]
-- description: Perfil Talento foundation-safe. C/P/A queda insufficient_data cuando el tenant no expone fuentes opcionales.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE is_active = TRUE
),
hier AS (
    SELECT user_id, direct_reports, depth
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_manager_hierarchy/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
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
    COALESCE(hier.direct_reports, 0) AS direct_reports,
    COALESCE(hier.depth, 0) AS hierarchy_depth,
    emp.start_date,
    emp.end_date,
    CASE
        WHEN emp.start_date IS NULL THEN NULL
        ELSE DATE_DIFF('month', TRY_CAST(emp.start_date AS DATE), CURRENT_DATE)
    END AS tenure_months,
    CAST(NULL AS DOUBLE) AS competency_score,
    CAST(NULL AS DOUBLE) AS performance_score,
    CAST(NULL AS DOUBLE) AS aspiration_score,
    'insufficient_data' AS cpa_status,
    'foundation_ready' AS profile_status,
    '["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN hier ON hier.user_id = emp.user_id
ORDER BY emp.user_id
