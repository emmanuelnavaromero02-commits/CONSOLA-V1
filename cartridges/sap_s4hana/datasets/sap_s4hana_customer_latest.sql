-- sap_s4hana_customer_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/Customer"]
-- description: Última extracción del maestro de clientes (A_Customer). El id de cliente llega shadowed desde bronze.

-- NOTA: A_Customer no declara select_fields; se tipan los campos de cabecera
-- estándar. Los datos fiscales/bancarios (TaxNumber*, IBAN…) viven en
-- sub-entidades y no se exponen aquí.
WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- Customer es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (Customer).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY Customer
                   ORDER BY load_date DESC, LastChangeDate DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Customer/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
