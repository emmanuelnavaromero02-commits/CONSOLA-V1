-- salesforce_account_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Account"]
-- description: Última versión de cada cuenta (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                            AS account_id,
    Name                          AS account_name,
    Industry                      AS industry,
    Type                          AS type,
    CAST(AnnualRevenue AS DOUBLE) AS annual_revenue,
    CAST(NumberOfEmployees AS BIGINT) AS number_of_employees,
    BillingCountry                AS billing_country,
    OwnerId                       AS owner_id,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Account/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
