-- sap_successfactors_candidate_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Candidate"]
-- description: Última extracción de candidatos (Recruiting). candidateId shadowed y nombre masked desde bronze.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    candidateId          AS candidate_id,       -- shadowed en bronze (FK)
    firstName            AS first_name,         -- masked en bronze
    lastName             AS last_name,          -- masked en bronze
    status               AS status,
    load_date
FROM latest
ORDER BY candidate_id
