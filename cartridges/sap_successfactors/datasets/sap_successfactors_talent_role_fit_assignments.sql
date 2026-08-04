-- sap_successfactors_talent_role_fit_assignments  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_readiness", "gold/sap_successfactors/sap_successfactors_talent_role_profile"]
-- description: Candidatos de movilidad por Fit Score. Recomendativo y bloqueado si faltan requisitos de rol.

WITH readiness AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_readiness/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
roles AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_role_profile/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    readiness.user_id,
    readiness.company_name,
    readiness.department_name,
    readiness.location_name,
    readiness.job_code AS current_job_code,
    readiness.role_name AS current_role_name,
    CASE
        WHEN COALESCE(readiness.invalid_score_input, TRUE)
          OR NOT COALESCE(
              readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
          )
          OR NOT talent_percent_is_valid(readiness.fit_score) THEN NULL
        ELSE readiness.fit_score
    END AS fit_score,
    readiness.readiness_status,
    (
        COALESCE(readiness.invalid_score_input, TRUE)
        OR NOT COALESCE(
            readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
        )
        OR NOT talent_percent_is_valid(readiness.fit_score)
    ) AS invalid_score_input,
    roles.required_skills_status,
    CASE
        WHEN COALESCE(readiness.invalid_score_input, TRUE)
          OR NOT COALESCE(
              readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
          )
          OR NOT talent_percent_is_valid(readiness.fit_score) THEN NULL
        WHEN readiness.fit_score < 60 THEN 'review_role_fit'
        WHEN readiness.fit_score >= 80 THEN 'succession_pool'
        ELSE 'development_plan'
    END AS assignment_recommendation,
    CASE
        WHEN COALESCE(readiness.invalid_score_input, TRUE)
          OR NOT COALESCE(
              readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
          )
          OR NOT talent_percent_is_valid(readiness.fit_score) THEN 'blocked'
        WHEN roles.required_skills_status = 'blocked' THEN 'partial'
        ELSE 'recommendation_only'
    END AS status,
    CASE
        WHEN COALESCE(readiness.invalid_score_input, TRUE)
          OR NOT COALESCE(
              readiness.readiness_status IN ('ready', 'near', 'not_ready'), FALSE
          )
          OR NOT talent_percent_is_valid(readiness.fit_score)
            THEN '["C/P/A invalid or missing for role fit"]'
        WHEN roles.required_skills_status = 'blocked' THEN '["Role required skills pending"]'
        ELSE '[]'
    END AS blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM readiness
LEFT JOIN roles ON roles.job_code = readiness.job_code
ORDER BY fit_score DESC NULLS LAST, readiness.user_id
