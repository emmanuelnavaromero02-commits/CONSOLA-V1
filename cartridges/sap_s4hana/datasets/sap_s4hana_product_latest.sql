-- sap_s4hana_product_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/Product"]
-- description: Última extracción del maestro de productos / materiales (A_Product).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Product/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/Product/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Product                          AS product,
    ProductType                      AS product_type,
    ProductGroup                     AS product_group,
    BaseUnit                         AS base_unit,
    CAST(CreationDate AS DATE)       AS creation_date,
    CAST(LastChangeDateTime AS TIMESTAMP) AS last_change,
    load_date
FROM latest
ORDER BY product
