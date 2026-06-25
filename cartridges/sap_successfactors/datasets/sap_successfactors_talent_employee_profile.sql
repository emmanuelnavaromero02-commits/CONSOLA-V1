-- sap_successfactors_talent_employee_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy", "silver/sap_successfactors/sap_successfactors_performance_cycle", "silver/sap_successfactors/sap_successfactors_employee_competency", "silver/sap_successfactors/sap_successfactors_employee_aspiration"]
-- description: Perfil Talento: empleado activo + estructura + C/P/A observado cuando el tenant expone datos.

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
),
performance AS (
    SELECT user_id, performance_rating AS performance_score
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performance_cycle/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
competency AS (
    SELECT
        user_id,
        AVG(proficiency_score) AS competency_score,
        COUNT(*) AS competency_count
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_competency/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE proficiency_score IS NOT NULL
    GROUP BY user_id
),
aspiration AS (
    SELECT
        user_id,
        MAX(aspiration_100) AS aspiration_score,
        COUNT(*) AS aspiration_count
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_aspiration/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    WHERE aspiration_100 IS NOT NULL
    GROUP BY user_id
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
    competency.competency_score,
    performance.performance_score,
    aspiration.aspiration_score,
    CASE
        WHEN competency.competency_score IS NOT NULL
             AND performance.performance_score IS NOT NULL
             AND aspiration.aspiration_score IS NOT NULL
            THEN 'ready'
        WHEN competency.competency_score IS NOT NULL
             OR performance.performance_score IS NOT NULL
             OR aspiration.aspiration_score IS NOT NULL
            THEN 'partial'
        ELSE 'insufficient_data'
    END AS cpa_status,
    'foundation_ready' AS profile_status,
    CASE
        WHEN competency.competency_score IS NOT NULL
             AND performance.performance_score IS NOT NULL
             AND aspiration.aspiration_score IS NOT NULL
            THEN '[]'
        WHEN competency.competency_score IS NULL
             AND performance.performance_score IS NULL
             AND aspiration.aspiration_score IS NULL
            THEN '["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"]'
        WHEN competency.competency_score IS NULL
             AND performance.performance_score IS NULL
            THEN '["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked"]'
        WHEN competency.competency_score IS NULL
             AND aspiration.aspiration_score IS NULL
            THEN '["KB-COMPETENCIAS blocked","KB-ASPIRACION blocked"]'
        WHEN performance.performance_score IS NULL
             AND aspiration.aspiration_score IS NULL
            THEN '["KB-DESEMPENO blocked","KB-ASPIRACION blocked"]'
        WHEN competency.competency_score IS NULL THEN '["KB-COMPETENCIAS blocked"]'
        WHEN performance.performance_score IS NULL THEN '["KB-DESEMPENO blocked"]'
        ELSE '["KB-ASPIRACION blocked"]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN hier ON hier.user_id = emp.user_id
LEFT JOIN performance ON performance.user_id = emp.user_id
LEFT JOIN competency ON competency.user_id = emp.user_id
LEFT JOIN aspiration ON aspiration.user_id = emp.user_id
ORDER BY emp.user_id
