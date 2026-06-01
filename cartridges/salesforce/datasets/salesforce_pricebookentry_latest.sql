-- salesforce_pricebookentry_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/PricebookEntry"]
-- description: Última versión de cada entrada de lista de precios (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                              AS entry_id,
    Product2Id                      AS product_id,
    Pricebook2Id                    AS pricebook_id,
    CAST(UnitPrice AS DOUBLE)       AS unit_price,
    CAST(IsActive AS BOOLEAN)       AS is_active,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/PricebookEntry/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
