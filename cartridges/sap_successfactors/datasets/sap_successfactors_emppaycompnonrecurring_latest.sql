-- sap_successfactors_emppaycompnonrecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompNonRecurring"]
-- description: Última extracción de pagos no recurrentes (bonos, pagos únicos). paycompValue encrypted desde bronze (caja negra; NO agregable).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompNonRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompNonRecurring/**/*.parquet',
                                          hive_partitioning = true))
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
