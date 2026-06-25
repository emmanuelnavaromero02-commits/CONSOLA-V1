-- sap_successfactors_talent_role_coverage  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_role_profile", "silver/sap_successfactors/sap_successfactors_role_requirements"]
-- description: Cobertura de perfiles de rol, posiciones y requisitos disponibles.

WITH roles AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
requirements AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_role_requirements/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
role_metrics AS (
    SELECT
        COUNT(DISTINCT job_code) AS job_codes_with_employees,
        SUM(active_employee_count) AS active_employees_covered,
        COUNT(*) FILTER (WHERE required_skills_status IN ('ready', 'partial')) AS roles_with_requirement_signal,
        COUNT(*) FILTER (WHERE required_skills_status = 'blocked') AS roles_blocked
    FROM roles
),
requirement_metrics AS (
    SELECT COUNT(DISTINCT role_id) AS roles_or_positions_defined
    FROM requirements
)
SELECT
    role_metrics.job_codes_with_employees,
    requirement_metrics.roles_or_positions_defined,
    role_metrics.active_employees_covered,
    role_metrics.roles_with_requirement_signal,
    role_metrics.roles_blocked,
    CASE
        WHEN role_metrics.roles_with_requirement_signal > 0 THEN 'partial'
        ELSE 'blocked'
    END AS role_coverage_status,
    CURRENT_TIMESTAMP AS generated_at
FROM role_metrics
CROSS JOIN requirement_metrics
