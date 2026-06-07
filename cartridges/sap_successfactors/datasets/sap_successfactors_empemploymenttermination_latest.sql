-- sap_successfactors_empemploymenttermination_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Última extracción de bajas (EmpEmploymentTermination). userId plano; la entidad está registrada para extracción.

-- Dedupe por empleado + fecha + motivo para conservar una baja real por evento
-- y descartar snapshots repetidos de Bronze.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmploymentTermination/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, endDate, eventReasonExternalCode
                ORDER BY
                    TRY_CAST(lastModifiedDateTime AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM raw
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId                     AS user_id,            -- plano
    CAST(endDate AS DATE)      AS termination_date,
    eventReasonExternalCode    AS event_reason,
    load_date
FROM latest
ORDER BY user_id, termination_date
