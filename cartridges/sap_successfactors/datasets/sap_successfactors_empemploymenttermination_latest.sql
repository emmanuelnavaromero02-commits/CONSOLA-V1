-- sap_successfactors_empemploymenttermination_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Última extracción de bajas (EmpEmploymentTermination). userId plano; la entidad está registrada para extracción.

-- Dedupe por empleado + fecha para conservar una baja real por evento
-- y descartar snapshots repetidos de Bronze.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmploymentTermination/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
normalized AS (
    SELECT
        raw.*,
        COALESCE(
            TRY_CAST(endDate AS DATE),
            CAST(
                to_timestamp(
                    TRY_CAST(regexp_extract(CAST(endDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
                ) AS DATE
            )
        ) AS _termination_date,
        COALESCE(
            TRY_CAST(lastModifiedDateTime AS TIMESTAMP),
            to_timestamp(
                TRY_CAST(regexp_extract(CAST(lastModifiedDateTime AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
            )
        ) AS _last_modified_at
    FROM raw
),
latest AS (
    SELECT *
    FROM (
        SELECT
            normalized.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, endDate
                ORDER BY
                    _last_modified_at DESC NULLS LAST,
                    TRY_CAST(_extracted_at AS TIMESTAMP) DESC NULLS LAST,
                    TRY_CAST(load_date AS DATE) DESC NULLS LAST,
                    CAST(batch_id AS VARCHAR) DESC NULLS LAST
            ) AS _rn
        FROM normalized
        WHERE userId IS NOT NULL
    )
    WHERE _rn = 1
)
SELECT
    userId                     AS user_id,            -- plano
    _termination_date          AS termination_date,
    CAST(NULL AS VARCHAR)      AS event_reason,       -- no visible por permisos OData en este tenant
    load_date
FROM latest
ORDER BY user_id, termination_date
