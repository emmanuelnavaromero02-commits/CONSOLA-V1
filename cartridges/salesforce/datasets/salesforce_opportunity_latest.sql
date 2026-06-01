-- salesforce_opportunity_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Opportunity"]
-- description: Última versión de cada oportunidad (dedup por Id sobre todas las cargas incrementales).
SELECT DISTINCT ON (Id)
    Id                        AS opportunity_id,
    Name                      AS opportunity_name,
    AccountId                 AS account_id,
    OwnerId                   AS owner_id,
    CAST(Amount AS DOUBLE)    AS amount,
    StageName                 AS stage_name,
    CAST(Probability AS DOUBLE) AS probability,
    CAST(CloseDate AS DATE)   AS close_date,
    Type                      AS type,
    LeadSource                AS lead_source,
    ForecastCategory          AS forecast_category,
    CAST(IsClosed AS BOOLEAN) AS is_closed,
    CAST(IsWon AS BOOLEAN)    AS is_won,
    CAST(CreatedDate AS TIMESTAMP) AS created_at,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Opportunity/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
