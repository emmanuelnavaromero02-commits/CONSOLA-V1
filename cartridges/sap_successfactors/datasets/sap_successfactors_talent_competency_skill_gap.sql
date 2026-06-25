-- sap_successfactors_talent_competency_skill_gap  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_employee_competency", "gold/sap_successfactors/sap_successfactors_talent_employee_profile", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: Cobertura de skills y gaps observados por rol para KB-COMPETENCIAS.

WITH skills AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_competency/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT job_code, role_name, required_skills_status
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
profile AS (
    SELECT user_id, job_code
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_employee_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
skill_by_role AS (
    SELECT
        profile.job_code,
        COUNT(DISTINCT skills.user_id) AS employees_with_skills,
        COUNT(DISTINCT skills.skill_id) AS distinct_skills,
        AVG(skills.proficiency_100) AS avg_proficiency_100
    FROM profile
    LEFT JOIN skills ON skills.user_id = profile.user_id
    GROUP BY profile.job_code
)
SELECT
    COALESCE(roles.job_code, '(sin rol)') AS job_code,
    COALESCE(roles.role_name, '(sin rol)') AS role_name,
    COALESCE(skill_by_role.employees_with_skills, 0) AS employees_with_skills,
    COALESCE(skill_by_role.distinct_skills, 0) AS distinct_skills,
    ROUND(skill_by_role.avg_proficiency_100, 2) AS avg_proficiency_100,
    COALESCE(roles.required_skills_status, 'blocked') AS required_skills_status,
    CASE
        WHEN COALESCE(skill_by_role.distinct_skills, 0) = 0 THEN 'insufficient_data'
        WHEN COALESCE(roles.required_skills_status, 'blocked') = 'blocked' THEN 'partial'
        ELSE 'ready'
    END AS skill_gap_status,
    CURRENT_TIMESTAMP AS generated_at
FROM roles
LEFT JOIN skill_by_role ON skill_by_role.job_code = roles.job_code
ORDER BY employees_with_skills DESC, role_name
