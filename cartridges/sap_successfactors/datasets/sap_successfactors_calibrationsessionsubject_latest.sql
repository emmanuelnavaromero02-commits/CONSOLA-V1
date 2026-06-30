-- sap_successfactors_calibrationsessionsubject_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CalibrationSessionSubject"]
-- description: Ultima extraccion de sujetos incluidos en sesiones de calibracion.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CalibrationSessionSubject/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY subjectId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE subjectId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    subjectId AS subject_id,
    sessionId AS session_id,
    userId AS user_id,
    TRY_CAST(performanceRating AS DOUBLE) AS performance_rating,
    TRY_CAST(potentialRating AS DOUBLE) AS potential_rating,
    load_date
FROM latest
ORDER BY session_id, user_id
