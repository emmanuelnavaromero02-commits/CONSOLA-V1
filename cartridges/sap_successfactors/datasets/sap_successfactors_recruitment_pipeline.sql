-- sap_successfactors_recruitment_pipeline  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "silver/sap_successfactors/sap_successfactors_jobrequisition_latest"]
-- description: Pipeline de reclutamiento centrado en la requisición. Candidate está bloqueado por permisos en este tenant; el conteo queda en 0 hasta habilitar JobApplication/Candidate.

-- TODO: el enlace requisición -> candidato está en JobApplication (no extraída).
-- Candidate no se consulta aquí porque la entidad requiere permisos OData
-- adicionales y bloquea la materialización del dataset.
WITH reqs AS (
    SELECT job_req_id, job_title, status, department, location
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_jobrequisition_latest/**/*.parquet')
)
SELECT
    r.job_req_id         AS job_req_id,
    r.job_title          AS job_title,
    r.status             AS status,
    r.department         AS department,
    r.location           AS location,
    CAST(0 AS BIGINT)    AS candidate_pool_total   -- TODO: por requisición vía JobApplication
FROM reqs r
ORDER BY r.job_req_id
