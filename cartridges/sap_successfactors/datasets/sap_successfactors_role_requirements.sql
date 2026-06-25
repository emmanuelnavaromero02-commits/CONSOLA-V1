-- sap_successfactors_role_requirements  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_position_latest", "silver/sap_successfactors/sap_successfactors_fojobcode_latest", "silver/sap_successfactors/sap_successfactors_competencyentity_latest"]
-- description: Requisitos de rol/posicion observables para KB-ROLES.

WITH positions AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_position_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
jobcodes AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_fojobcode_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
competencies AS (
    SELECT COUNT(*) AS competency_catalog_count
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_competencyentity_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    p.position_id AS role_id,
    p.position_name AS role_name,
    CAST(NULL AS VARCHAR) AS job_code,
    p.department,
    p.location,
    p.cost_center,
    c.competency_catalog_count,
    CASE WHEN c.competency_catalog_count > 0 THEN 'partial' ELSE 'blocked' END AS required_skills_status,
    CASE
        WHEN c.competency_catalog_count > 0 THEN '["Role skill requirement mapping pending"]'
        ELSE '["Skills/competencies metadata pending"]'
    END AS blockers,
    p.load_date
FROM positions p
CROSS JOIN competencies c
UNION ALL
SELECT
    j.job_code AS role_id,
    j.job_name AS role_name,
    j.job_code,
    CAST(NULL AS VARCHAR) AS department,
    CAST(NULL AS VARCHAR) AS location,
    CAST(NULL AS VARCHAR) AS cost_center,
    c.competency_catalog_count,
    CASE WHEN c.competency_catalog_count > 0 THEN 'partial' ELSE 'blocked' END AS required_skills_status,
    CASE
        WHEN c.competency_catalog_count > 0 THEN '["Role skill requirement mapping pending"]'
        ELSE '["Skills/competencies metadata pending"]'
    END AS blockers,
    j.load_date
FROM jobcodes j
CROSS JOIN competencies c
ORDER BY role_name
