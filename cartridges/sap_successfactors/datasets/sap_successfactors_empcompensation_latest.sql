-- sap_successfactors_empcompensation_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpCompensation"]
-- description: Última extracción de EmpCompensation (cabecera de compensación: grupo de pago, frecuencia). userId plano.

-- NOTA: EmpCompensation no declara select_fields; campos SF estándar de cabecera.
WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpCompensation/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, startDate
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
    userId               AS user_id,            -- plano
    CAST(
        COALESCE(
            TRY_CAST(startDate AS TIMESTAMP),
            to_timestamp(
                TRY_CAST(regexp_extract(CAST(startDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS BIGINT) / 1000
            )
        ) AS DATE
    ) AS start_date,
    payGroup             AS pay_group,
    CAST(NULL AS VARCHAR) AS frequency_code,
    load_date
FROM latest
ORDER BY user_id, start_date
