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
        -- Profile scores are already percentages. Preserve them byte-for-value;
        -- only the 9-box banding step converts percentages to the 0..5 scale.
        CASE WHEN talent_percent_is_valid(emp.competency_score)
            THEN TRY_CAST(emp.competency_score AS DOUBLE) END AS competency_score,
        CASE WHEN talent_percent_is_valid(emp.performance_score)
            THEN TRY_CAST(emp.performance_score AS DOUBLE) END AS performance_score,
        CASE WHEN talent_percent_is_valid(emp.aspiration_score)
            THEN TRY_CAST(emp.aspiration_score AS DOUBLE) END AS aspiration_score,
        CASE WHEN talent_percent_is_valid(emp.competency_score)
            THEN TRY_CAST(emp.competency_score AS DOUBLE) END AS competency_100,
        CASE WHEN talent_percent_is_valid(emp.performance_score)
            THEN TRY_CAST(emp.performance_score AS DOUBLE) END AS performance_100,
        CASE WHEN talent_percent_is_valid(emp.aspiration_score)
            THEN TRY_CAST(emp.aspiration_score AS DOUBLE) END AS aspiration_100,
        (
            COALESCE(emp.invalid_score_input, FALSE)
            OR (emp.competency_score IS NOT NULL
                AND NOT talent_percent_is_valid(emp.competency_score))
            OR (emp.performance_score IS NOT NULL
                AND NOT talent_percent_is_valid(emp.performance_score))
            OR (emp.aspiration_score IS NOT NULL
                AND NOT talent_percent_is_valid(emp.aspiration_score))
        ) AS invalid_score_input,
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
    invalid_score_input,
    CASE
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL THEN NULL
        ELSE ROUND((0.45 * competency_100) + (0.30 * performance_100) + (0.25 * aspiration_100), 2)
    END AS fit_score,
    CASE
        WHEN invalid_score_input THEN 'blocked'
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL THEN 'insufficient_data'
        WHEN required_skills_status = 'blocked' THEN 'partial'
        ELSE 'ready'
    END AS cpa_status,
    role_profile_status,
    required_skills_status,
    CASE
        WHEN invalid_score_input THEN '["invalid_score_input"]'
        WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL
            -- GATE 3 (Fase B): blockers CONDICIONALES por componente — cada KB solo
            -- se lista si su score falta. Antes se emitian los 3 en bloque, marcando
            -- KB-DESEMPENO como bloqueado aun con performance_100 presente.
            THEN to_json(list_filter([
                    CASE WHEN competency_100 IS NULL THEN 'KB-COMPETENCIAS blocked' END,
                    CASE WHEN performance_100 IS NULL THEN 'KB-DESEMPENO blocked' END,
                    CASE WHEN aspiration_100 IS NULL THEN 'KB-ASPIRACION blocked' END
                ], x -> x IS NOT NULL))::VARCHAR
        WHEN required_skills_status = 'blocked'
            THEN '["Position requirements pending","Skills/competencies metadata pending"]'
        ELSE '[]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM normalized
ORDER BY user_id
