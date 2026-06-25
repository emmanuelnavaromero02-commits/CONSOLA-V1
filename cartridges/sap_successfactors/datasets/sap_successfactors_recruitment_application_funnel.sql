-- sap_successfactors_recruitment_application_funnel  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_job_application_pipeline"]
-- description: Embudo Recruiting por etapa de aplicacion, departamento y fuente.

WITH pipe AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_job_application_pipeline/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    COALESCE(department, '(sin departamento)') AS department,
    COALESCE(application_status, '(sin etapa)') AS application_status,
    COALESCE(source, '(sin fuente)') AS source,
    COUNT(DISTINCT job_req_id) AS requisitions,
    COUNT(DISTINCT application_id) AS applications,
    COUNT(DISTINCT candidate_id) AS candidates,
    CASE WHEN COUNT(DISTINCT application_id) = 0 THEN 'partial' ELSE 'ready' END AS application_funnel_status,
    CURRENT_TIMESTAMP AS generated_at
FROM pipe
GROUP BY department, application_status, source
ORDER BY applications DESC, department, application_status
