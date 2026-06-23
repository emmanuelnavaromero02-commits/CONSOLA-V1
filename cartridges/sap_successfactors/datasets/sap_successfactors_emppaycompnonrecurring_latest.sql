-- sap_successfactors_emppaycompnonrecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompNonRecurring"]
-- description: Última extracción de pagos no recurrentes (bonos, pagos únicos). paycompValue encrypted desde bronze (caja negra; NO agregable).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompNonRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
normalized AS (
    SELECT
        raw.*,
        COALESCE(
            TRY_CAST(payDate AS DATE),
            CAST(
                to_timestamp(
                    TRY_CAST(regexp_extract(CAST(payDate AS VARCHAR), '^/Date\((-?[0-9]+)', 1) AS DOUBLE) / 1000
                ) AS DATE
            )
        ) AS _pay_date,
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
                PARTITION BY userId, payComponentCode, payDate
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
    payComponentCode     AS pay_component,
    value                AS paycomp_value,      -- encrypted en bronze (no agregable)
    currencyCode         AS currency,
    _pay_date            AS pay_date,
    load_date
FROM latest
ORDER BY user_id, pay_date
