-- sap_successfactors_talent_readiness  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: Readiness Talento. En v1 conserva contrato estable y marca insufficient_data si faltan competencia, desempeno o aspiracion.

WITH emp AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, role_name, role_profile_status, required_skills_status
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    emp.user_id,
    emp.full_name,
    emp.company_name,
    emp.department_name,
    emp.location_name,
    emp.job_code,
    COALESCE(roles.role_name, emp.job_code) AS role_name,
    emp.competency_score,
    emp.performance_score,
    emp.aspiration_score,
    CAST(NULL AS DOUBLE) AS fit_score,
    'insufficient_data' AS readiness_status,
    'Insufficient data' AS readiness_label,
    COALESCE(roles.role_profile_status, 'partial') AS role_profile_status,
    COALESCE(roles.required_skills_status, 'blocked') AS required_skills_status,
    3 AS blocker_count,
    emp.blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM emp
LEFT JOIN roles ON roles.job_code = emp.job_code
ORDER BY emp.user_id
