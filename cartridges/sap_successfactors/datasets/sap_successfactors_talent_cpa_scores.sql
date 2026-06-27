-- sap_successfactors_talent_cpa_scores  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: C/P/A normalizado y Fit Score WB-TALENTO. Calcula solo si competencia, desempeno y aspiracion existen; si no, conserva insufficient_data.

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
),
normalized AS (
    SELECT
        emp.tenant_id,
        emp.workspace_id,
        emp.user_id,
        emp.full_name,
        emp.company_name,
        emp.department_name,
        emp.location_name,
        emp.job_code,
        emp.direct_reports,
        emp.tenure_months,
        COALESCE(roles.role_name, emp.job_code) AS role_name,
        TRY_CAST(emp.competency_score AS DOUBLE) AS competency_score,
        TRY_CAST(emp.performance_score AS DOUBLE) AS performance_score,
        TRY_CAST(emp.aspiration_score AS DOUBLE) AS aspiration_score,
        CASE
            WHEN TRY_CAST(emp.competency_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(emp.competency_score AS DOUBLE) <= 5 THEN TRY_CAST(emp.competency_score AS DOUBLE) * 20
            ELSE TRY_CAST(emp.competency_score AS DOUBLE)
        END AS competency_100,
        CASE
            WHEN TRY_CAST(emp.performance_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(emp.performance_score AS DOUBLE) <= 5 THEN TRY_CAST(emp.performance_score AS DOUBLE) * 20
            ELSE TRY_CAST(emp.performance_score AS DOUBLE)
        END AS performance_100,
        CASE
            WHEN TRY_CAST(emp.aspiration_score AS DOUBLE) IS NULL THEN NULL
            WHEN TRY_CAST(emp.aspiration_score AS DOUBLE) <= 5 THEN TRY_CAST(emp.aspiration_score AS DOUBLE) * 20
            ELSE TRY_CAST(emp.aspiration_score AS DOUBLE)
        END AS aspiration_100,
        COALESCE(roles.role_profile_status, 'partial') AS role_profile_status,
        COALESCE(roles.required_skills_status, 'blocked') AS required_skills_status,
        emp.blockers AS source_blockers
    FROM emp
    LEFT JOIN roles ON roles.job_code = emp.job_code
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
    role_name,
    competency_score,
    performance_score,
    aspiration_score,
    competency_100,
    performance_100,
    aspiration_100,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL THEN NULL
        ELSE ROUND((0.45 * competency_100) + (0.30 * performance_100) + (0.25 * aspiration_100), 2)
    END AS fit_score,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL THEN 'insufficient_data'
        WHEN required_skills_status = 'blocked' THEN 'partial'
        ELSE 'ready'
    END AS cpa_status,
    role_profile_status,
    required_skills_status,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL
            THEN '["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"]'
        WHEN required_skills_status = 'blocked'
            THEN '["Position requirements pending","Skills/competencies metadata pending"]'
        ELSE '[]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM normalized
ORDER BY user_id
