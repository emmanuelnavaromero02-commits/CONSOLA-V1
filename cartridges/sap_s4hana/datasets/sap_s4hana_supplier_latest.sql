-- sap_s4hana_supplier_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/Supplier"]
-- description: Última extracción del maestro de proveedores (A_Supplier). El id de proveedor llega shadowed desde bronze.

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- Supplier es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (Supplier).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY Supplier
                   ORDER BY load_date DESC, LastChangeDate DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Supplier/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
)
SELECT
    Supplier                      AS supplier,                -- shadowed en bronze (FK estable)
    SupplierName                  AS supplier_name,
    SupplierFullName              AS supplier_full_name,
    SupplierAccountGroup          AS account_group,
    CAST(LastChangeDate AS DATE)  AS last_change_date,
    load_date
FROM latest
ORDER BY supplier
