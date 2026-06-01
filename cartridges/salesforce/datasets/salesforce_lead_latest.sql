-- salesforce_lead_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Lead"]
-- description: Última versión de cada lead (dedup por Id). Nombre/email/teléfono masked en bronze.
SELECT DISTINCT ON (Id)
    Id                          AS lead_id,
    Company                     AS company,
    Status                      AS status,
    LeadSource                  AS lead_source,
    CAST(IsConverted AS BOOLEAN) AS is_converted,
    OwnerId                     AS owner_id,
    CAST(CreatedDate AS TIMESTAMP) AS created_at,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Lead/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
