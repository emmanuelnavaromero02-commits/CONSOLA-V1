-- sap_successfactors_candidate_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Candidate"]
-- description: Última extracción de candidatos (Recruiting). candidateId shadowed y nombre masked desde bronze.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY candidateId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE candidateId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    candidateId          AS candidate_id,       -- shadowed en bronze (FK)
    firstName            AS first_name,         -- masked en bronze
    lastName             AS last_name,          -- masked en bronze
    -- Candidate.status is not exposed by the tenant OData metadata. Preserve
    -- the Silver schema without inferring a recruiting state from other fields.
    CAST(NULL AS VARCHAR) AS status,
    load_date
FROM latest
ORDER BY candidate_id
