-- sap_successfactors_emppaycomprecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompRecurring"]
-- description: Última extracción de pagos recurrentes (salario base, complementos). paycompValue llega encrypted desde bronze (caja negra; NO agregable en SQL).

WITH raw AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
),
latest AS (
    SELECT *
    FROM (
        SELECT
            raw.*,
            ROW_NUMBER() OVER (
                PARTITION BY userId, payComponent, startDate
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
    frequency            AS frequency,
    currency             AS currency,
    CAST(startDate AS DATE) AS start_date,
    load_date
FROM latest
ORDER BY user_id, pay_component, start_date
