-- sap_successfactors_talent_readiness  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_talent_cpa_scores"]
-- description: Readiness Talento WB-TALENTO. Calcula Ready/Near/Not cuando C/P/A existe; si falta, devuelve insufficient_data.

WITH cpa AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_talent_cpa_scores/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    cpa.user_id,
    cpa.full_name,
    cpa.company_name,
    cpa.department_name,
    cpa.location_name,
    cpa.job_code,
    cpa.role_name,
    cpa.competency_score,
    cpa.performance_score,
    cpa.aspiration_score,
    cpa.fit_score,
    CASE
        WHEN cpa.fit_score IS NULL THEN 'insufficient_data'
        WHEN cpa.fit_score >= 80 THEN 'ready'
        WHEN cpa.fit_score >= 60 THEN 'near'
        ELSE 'not_ready'
    END AS readiness_status,
    CASE
        WHEN cpa.fit_score IS NULL THEN 'Insufficient data'
        WHEN cpa.fit_score >= 80 THEN 'Ready'
        WHEN cpa.fit_score >= 60 THEN 'Near'
        ELSE 'Not ready'
    END AS readiness_label,
    cpa.role_profile_status,
    cpa.required_skills_status,
    CASE
        WHEN cpa.fit_score IS NULL THEN 3
        WHEN cpa.required_skills_status = 'blocked' THEN 1
        ELSE 0
    END AS blocker_count,
    cpa.blockers,
    CURRENT_TIMESTAMP AS generated_at
FROM cpa
ORDER BY cpa.user_id
