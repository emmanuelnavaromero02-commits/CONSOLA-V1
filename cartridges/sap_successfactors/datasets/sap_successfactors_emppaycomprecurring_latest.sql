-- sap_successfactors_emppaycomprecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompRecurring"]
-- description: Última extracción de pagos recurrentes (salario base, complementos). paycompValue llega encrypted desde bronze (caja negra; NO agregable en SQL).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
normalized AS (
    SELECT
        raw.*,
        COALESCE(
            TRY_CAST(startDate AS DATE),
            CAST(
                to_timestamp(
                    TRY_CAST(regexp_extract(CAST(startDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
                ) AS DATE
            )
        ) AS _start_date,
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
                PARTITION BY userId, payComponent, startDate
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
    userId               AS user_id,            -- plano
    payComponent         AS pay_component,
    paycompvalue         AS paycomp_value,      -- encrypted en bronze (no agregable)
    frequency            AS frequency,
    currencyCode         AS currency,
    _start_date          AS start_date,
    load_date
FROM latest
ORDER BY user_id, pay_component, start_date
