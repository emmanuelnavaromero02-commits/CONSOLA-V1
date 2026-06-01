-- salesforce_opportunitylineitem_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/OpportunityLineItem"]
-- description: Última versión de cada línea de oportunidad (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                          AS line_item_id,
    OpportunityId               AS opportunity_id,
    Product2Id                  AS product_id,
    CAST(Quantity AS DOUBLE)    AS quantity,
    CAST(UnitPrice AS DOUBLE)   AS unit_price,
    CAST(TotalPrice AS DOUBLE)  AS total_price,
    CAST(ListPrice AS DOUBLE)   AS list_price,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/OpportunityLineItem/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
