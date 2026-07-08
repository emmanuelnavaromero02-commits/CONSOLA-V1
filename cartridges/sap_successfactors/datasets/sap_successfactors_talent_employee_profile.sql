-- sap_successfactors_talent_employee_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "gold/sap_successfactors/sap_successfactors_manager_hierarchy", "silver/sap_successfactors/sap_successfactors_performance_cycle", "silver/sap_successfactors/sap_successfactors_employee_competency", "silver/sap_successfactors/sap_successfactors_employee_aspiration"]
-- description: Perfil Talento foundation-safe con C/P/A real cuando el tenant expone las fuentes.

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
-- Los CTEs de talento se agregan por user_id_hash (clave tecnica shadowed) para
-- poder unir con employee_360.user_id_hash. performance_cycle ya expone
-- user_id_hash; competency/aspiration traen su user_id shadowed (= el hash).
performance AS (
    SELECT
        user_id_hash,
        AVG(
            CASE
                WHEN performance_rating IS NULL THEN NULL
                WHEN performance_rating <= 5 THEN performance_rating * 20
                ELSE performance_rating
            END
        ) AS performance_score,
        BOOL_OR(performance_status = 'ready') AS has_performance
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_performance_cycle/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    GROUP BY user_id_hash
),
competency AS (
    SELECT
        user_id AS user_id_hash,
        AVG(proficiency_100) AS competency_score,
        BOOL_OR(competency_status = 'ready') AS has_competency
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_competency/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    GROUP BY user_id
),
aspiration AS (
    SELECT
        user_id AS user_id_hash,
        AVG(aspiration_100) AS aspiration_score,
        BOOL_OR(aspiration_status = 'ready') AS has_aspiration
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_aspiration/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    GROUP BY user_id
),
profile AS (
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
        COALESCE(performance.has_performance, FALSE) AS has_performance,
        COALESCE(competency.has_competency, FALSE) AS has_competency,
        COALESCE(aspiration.has_aspiration, FALSE) AS has_aspiration
    FROM emp
    LEFT JOIN hier ON hier.user_id = emp.user_id
    -- Talento une por user_id_hash (shadowed en ambos lados); manager_hierarchy
    -- usa user_id crudo de foundation, que si coincide con emp.user_id.
    LEFT JOIN performance ON performance.user_id_hash = emp.user_id_hash
    LEFT JOIN competency ON competency.user_id_hash = emp.user_id_hash
    LEFT JOIN aspiration ON aspiration.user_id_hash = emp.user_id_hash
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
    direct_reports,
    hierarchy_depth,
    start_date,
    end_date,
    tenure_months,
    competency_score,
    performance_score,
    aspiration_score,
    CASE
        WHEN has_performance AND has_competency AND has_aspiration THEN 'ready'
        WHEN has_performance OR has_competency OR has_aspiration THEN 'partial'
        ELSE 'insufficient_data'
    END AS cpa_status,
    'foundation_ready' AS profile_status,
    '[' ||
        CONCAT_WS(
            ',',
            CASE WHEN has_competency THEN NULL ELSE '"missing_competency"' END,
            CASE WHEN has_performance THEN NULL ELSE '"missing_performance"' END,
            CASE WHEN has_aspiration THEN NULL ELSE '"missing_aspiration"' END
        ) ||
    ']' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM profile
ORDER BY user_id
