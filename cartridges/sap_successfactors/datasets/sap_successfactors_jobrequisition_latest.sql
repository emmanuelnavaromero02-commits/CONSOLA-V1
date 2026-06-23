-- sap_successfactors_jobrequisition_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/JobRequisition"]
-- description: Última extracción de requisiciones de empleo (Recruiting).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/JobRequisition/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY jobReqId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE jobReqId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    jobReqId             AS job_req_id,
    jobTitle             AS job_title,
    status               AS status,
    department           AS department,
    location             AS location,
    load_date
FROM latest
ORDER BY job_req_id
