-- sap_successfactors_calibrationsession_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/CalibrationSession"]
-- description: Ultima extraccion de sesiones de calibracion.

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/CalibrationSession/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY sessionId
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE sessionId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    sessionId AS session_id,
    name AS session_name,
    status AS session_status,
    TRY_CAST(startDate AS DATE) AS start_date,
    TRY_CAST(endDate AS DATE) AS end_date,
    load_date
FROM latest
ORDER BY session_id
