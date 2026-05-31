-- salesforce_campaignmember_latest  (silver)  cartridge: salesforce
-- sources: ["raw/salesforce/CampaignMember"]
-- description: Última versión de cada miembro de campaña (dedup por Id).
--   LeadId o ContactId puede ser NULL según el tipo de miembro.
SELECT DISTINCT ON (Id)
    Id                              AS member_id,
    CampaignId                      AS campaign_id,
    LeadId                          AS lead_id,
    ContactId                       AS contact_id,
    Status                          AS status,
    load_date
FROM read_parquet('s3://{bucket}/raw/salesforce/CampaignMember/**/*.parquet',
                  hive_partitioning = true, union_by_name = true)
ORDER BY Id, load_date DESC
