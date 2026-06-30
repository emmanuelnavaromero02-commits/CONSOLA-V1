-- sap_successfactors_calibrationsubjectrank_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CalibrationSubjectRank"]
-- description: Ultima extraccion de rankings calibrados por sujeto.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CalibrationSubjectRank/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY rankId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE rankId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    rankId AS rank_id,
    sessionId AS session_id,
    subjectId AS subject_id,
    userId AS user_id,
    rank AS calibration_rank,
    load_date
FROM latest
ORDER BY session_id, user_id
