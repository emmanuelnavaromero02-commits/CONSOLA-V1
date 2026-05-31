-- salesforce_product2_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Product2"]
-- description: Última versión de cada producto del catálogo (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                              AS product_id,
    Name                            AS product_name,
    ProductCode                     AS product_code,
    Family                          AS family,
    CAST(IsActive AS BOOLEAN)       AS is_active,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Product2/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
