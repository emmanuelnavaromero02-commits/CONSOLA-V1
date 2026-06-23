-- sap_successfactors_emppaycompnonrecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompNonRecurring"]
-- description: Última extracción de pagos no recurrentes (bonos, pagos únicos). paycompValue encrypted desde bronze (caja negra; NO agregable).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompNonRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, payComponent, payDate
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
    payComponent         AS pay_component,
    paycompValue         AS paycomp_value,      -- encrypted en bronze (no agregable)
    currency             AS currency,
    CAST(payDate AS DATE) AS pay_date,
    load_date
FROM latest
ORDER BY user_id, pay_date
