-- salesforce_campaign_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Campaign"]
-- description: Última versión de cada campaña de marketing (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                                  AS campaign_id,
    Name                                AS campaign_name,
    Type                                AS type,
    Status                              AS status,
    CAST(StartDate AS DATE)             AS start_date,
    CAST(EndDate AS DATE)               AS end_date,
    CAST(BudgetedCost AS DOUBLE)        AS budgeted_cost,
    CAST(ActualCost AS DOUBLE)          AS actual_cost,
    CAST(NumberOfLeads AS BIGINT)       AS number_of_leads,
    CAST(NumberOfOpportunities AS BIGINT) AS number_of_opportunities,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Campaign/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
