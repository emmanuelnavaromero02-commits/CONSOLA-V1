-- sap_successfactors_recruitment_pipeline  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition", "raw/sap_successfactors/Candidate"]
-- description: Pipeline de reclutamiento centrado en la requisición. El conteo de candidatos por requisición queda pendiente: la relación req <-> candidato vive en JobApplication (no extraída).

-- TODO: el enlace requisición -> candidato está en JobApplication (no extraída).
-- Hoy se listan las requisiciones; candidate_count es global (no por req) como
-- referencia, marcado para sustituir cuando exista JobApplication.
WITH reqs AS (
    SELECT job_req_id, job_title, status, department, location
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_jobrequisition_latest/**/*.parquet')
),
candidate_total AS (
    SELECT COUNT(*) AS total_candidates
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_candidate_latest/**/*.parquet')
)
SELECT
    r.job_req_id         AS job_req_id,
    r.job_title          AS job_title,
    r.status             AS status,
    r.department         AS department,
    r.location           AS location,
    ct.total_candidates  AS candidate_pool_total   -- TODO: por requisición vía JobApplication
FROM reqs r
CROSS JOIN candidate_total ct
ORDER BY r.job_req_id
