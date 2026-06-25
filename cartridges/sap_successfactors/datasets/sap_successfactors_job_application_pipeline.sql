-- sap_successfactors_job_application_pipeline  (silver)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_jobrequisition_latest", "silver/sap_successfactors/sap_successfactors_jobapplication_latest", "silver/sap_successfactors/sap_successfactors_candidate_latest"]
-- description: Pipeline Recruiting por requisicion con candidatos y etapas desde JobApplication.

WITH reqs AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_jobrequisition_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
apps AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_jobapplication_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
candidates AS (
    SELECT candidate_id
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_candidate_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    r.job_req_id,
    r.job_title,
    r.status AS requisition_status,
    r.department,
    r.location,
    a.application_id,
    a.candidate_id,
    a.application_status,
    a.source,
    CASE WHEN c.candidate_id IS NULL THEN 'candidate_missing' ELSE 'ready' END AS candidate_status,
    r.load_date
FROM reqs r
LEFT JOIN apps a ON a.job_req_id = r.job_req_id
LEFT JOIN candidates c ON c.candidate_id = a.candidate_id
ORDER BY r.job_req_id, a.application_id
