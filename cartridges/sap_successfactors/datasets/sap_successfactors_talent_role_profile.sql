-- sap_successfactors_talent_role_profile  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360", "silver/sap_successfactors/sap_successfactors_fojobcode_latest"]
-- description: Perfil de rol derivado desde job_code/FOJobCode. Requisitos de skills quedan bloqueados hasta validar metadata del tenant.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, job_name
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fojobcode_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
rollup AS (
    SELECT
        emp.job_code,
        COUNT(*) AS employee_count,
        COUNT(*) FILTER (WHERE emp.is_active = TRUE) AS active_employee_count,
        COUNT(DISTINCT emp.department_id) AS departments_count,
        COUNT(DISTINCT emp.location_id) AS locations_count,
        COUNT(DISTINCT emp.company_id) AS companies_count
    FROM emp
    WHERE emp.job_code IS NOT NULL
    GROUP BY emp.job_code
)
SELECT
    rollup.job_code,
    COALESCE(roles.job_name, rollup.job_code) AS role_name,
    rollup.employee_count,
    rollup.active_employee_count,
    rollup.departments_count,
    rollup.locations_count,
    rollup.companies_count,
    'blocked' AS required_skills_status,
    'partial' AS role_profile_status,
    '["Position requirements pending","Skills/competencies metadata pending"]' AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM rollup
LEFT JOIN roles ON roles.job_code = rollup.job_code
ORDER BY rollup.active_employee_count DESC, rollup.job_code
