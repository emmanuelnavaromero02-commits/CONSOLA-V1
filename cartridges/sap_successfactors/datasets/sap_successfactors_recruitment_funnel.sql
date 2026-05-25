-- sap_successfactors_recruitment_funnel  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "raw/sap_successfactors/Candidate"]
-- description: Embudo de reclutamiento por departamento: requisiciones totales y abiertas. Las etapas por candidato requieren JobApplication (no extraída).

-- TODO: el detalle por etapa (aplicado -> entrevista -> oferta) requiere
-- JobApplication (no extraída). Aquí se reportan contadores de requisición.
WITH pipe AS (
    SELECT job_req_id, department, status, candidate_pool_total
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_recruitment_pipeline/**/*.parquet')
)
SELECT
    COALESCE(department, '(sin departamento)')                                   AS department,
    COUNT(DISTINCT job_req_id)                                                   AS requisitions,
    COUNT(DISTINCT CASE WHEN status IS DISTINCT FROM 'Closed' AND status IS DISTINCT FROM 'Filled' THEN job_req_id END) AS open_requisitions,
    MAX(candidate_pool_total)                                                    AS candidate_pool
FROM pipe
GROUP BY department
ORDER BY open_requisitions DESC, department
