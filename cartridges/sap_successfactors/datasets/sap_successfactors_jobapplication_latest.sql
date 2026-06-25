-- sap_successfactors_jobapplication_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobApplication"]
-- description: Aplicaciones Recruiting para unir requisicion, candidato y etapa.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/JobApplication/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY applicationId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE applicationId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    applicationId AS application_id,
    jobReqId AS job_req_id,
    candidateId AS candidate_id,
    applicationStatus AS application_status,
    source,
    load_date
FROM latest
ORDER BY job_req_id, application_id
