-- sap_successfactors_recruitment_pipeline  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "silver/sap_successfactors/sap_successfactors_jobrequisition_latest", "silver/sap_successfactors/sap_successfactors_job_application_pipeline"]
-- description: Pipeline de reclutamiento centrado en la requisicion; usa JobApplication cuando esta disponible.

WITH reqs AS (
    SELECT job_req_id, job_title, status, department, location
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_jobrequisition_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
apps AS (
    SELECT
        job_req_id,
        COUNT(DISTINCT application_id) AS applications_total,
        COUNT(DISTINCT candidate_id) AS candidate_pool_total
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_job_application_pipeline/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
    GROUP BY job_req_id
)
SELECT
    r.job_req_id         AS job_req_id,
    r.job_title          AS job_title,
    r.status             AS status,
    r.department         AS department,
    r.location           AS location,
    COALESCE(a.applications_total, 0) AS applications_total,
    COALESCE(a.candidate_pool_total, 0) AS candidate_pool_total,
    CASE WHEN a.applications_total IS NULL THEN 'partial' ELSE 'ready' END AS recruiting_status
FROM reqs r
LEFT JOIN apps a ON a.job_req_id = r.job_req_id
ORDER BY r.job_req_id
