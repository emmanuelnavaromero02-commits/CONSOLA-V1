-- salesforce_contact_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/Contact"]
-- description: Última versión de cada contacto (dedup por Id).
--   FirstName/LastName/Email/Phone están masked en bronze; no se exponen en silver.
--   Id ya viene shadowed (SHA-256) desde el layer de extracción.
SELECT DISTINCT ON (Id)
    Id                              AS contact_id,
    AccountId                       AS account_id,
    Title                           AS title,
    OwnerId                         AS owner_id,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/Contact/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
