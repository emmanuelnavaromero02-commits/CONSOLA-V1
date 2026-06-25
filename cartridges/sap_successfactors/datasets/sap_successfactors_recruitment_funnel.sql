-- sap_successfactors_recruitment_funnel  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "raw/sap_successfactors/Candidate", "silver/sap_successfactors/sap_successfactors_recruitment_pipeline"]
-- description: Embudo de reclutamiento por departamento con candidate pool cuando JobApplication esta disponible.

WITH pipe AS (
    SELECT job_req_id, department, status, applications_total, candidate_pool_total, recruiting_status
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_recruitment_pipeline/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    COALESCE(department, '(sin departamento)')                                   AS department,
    COUNT(DISTINCT job_req_id)                                                   AS requisitions,
    COUNT(DISTINCT CASE WHEN status IS DISTINCT FROM 'Closed' AND status IS DISTINCT FROM 'Filled' THEN job_req_id END) AS open_requisitions,
    SUM(applications_total)                                                       AS applications_total,
    SUM(candidate_pool_total)                                                     AS candidate_pool,
    CASE WHEN COUNT(*) FILTER (WHERE recruiting_status = 'ready') > 0 THEN 'ready' ELSE 'partial' END AS funnel_status
FROM pipe
GROUP BY department
ORDER BY open_requisitions DESC, department
