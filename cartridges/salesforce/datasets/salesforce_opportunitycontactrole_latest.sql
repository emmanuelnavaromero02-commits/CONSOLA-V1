-- salesforce_opportunitycontactrole_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/OpportunityContactRole"]
-- description: Última versión de cada rol de contacto en oportunidad (dedup por Id).
SELECT DISTINCT ON (Id)
    Id                              AS role_id,
    OpportunityId                   AS opportunity_id,
    ContactId                       AS contact_id,
    Role                            AS role,
    CAST(IsPrimary AS BOOLEAN)      AS is_primary,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/OpportunityContactRole/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
