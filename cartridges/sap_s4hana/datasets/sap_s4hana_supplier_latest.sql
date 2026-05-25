-- sap_s4hana_supplier_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/Supplier"]
-- description: Última extracción del maestro de proveedores (A_Supplier). El id de proveedor llega shadowed desde bronze.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Supplier/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Supplier/**/*.parquet',
                                          hive_partitioning = true))
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
