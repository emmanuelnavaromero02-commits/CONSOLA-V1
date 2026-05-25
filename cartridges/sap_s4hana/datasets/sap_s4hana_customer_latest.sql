-- sap_s4hana_customer_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/Customer"]
-- description: Última extracción del maestro de clientes (A_Customer). El id de cliente llega shadowed desde bronze.

-- NOTA: A_Customer no declara select_fields; se tipan los campos de cabecera
-- estándar. Los datos fiscales/bancarios (TaxNumber*, IBAN…) viven en
-- sub-entidades y no se exponen aquí.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Customer/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Customer/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Customer                      AS customer,                -- shadowed en bronze (FK estable)
    CustomerName                  AS customer_name,
    CustomerFullName              AS customer_full_name,
    CustomerAccountGroup          AS account_group,
    CAST(LastChangeDate AS DATE)  AS last_change_date,
    load_date
FROM latest
ORDER BY customer
