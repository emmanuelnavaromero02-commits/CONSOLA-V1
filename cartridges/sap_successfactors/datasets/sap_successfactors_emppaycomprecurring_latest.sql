-- sap_successfactors_emppaycomprecurring_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpPayCompRecurring"]
-- description: Última extracción de pagos recurrentes (salario base, complementos). paycompValue llega encrypted desde bronze (caja negra; NO agregable en SQL).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompRecurring/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpPayCompRecurring/**/*.parquet',
                                          hive_partitioning = true))
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
