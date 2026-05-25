-- sap_s4hana_businesspartner_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BusinessPartner"]
-- description: Última extracción de Business Partners (maestro) con campos tipados. El id y el nombre llegan ya protegidos desde bronze (shadowed / masked).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartner/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartner/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BusinessPartner               AS business_partner,        -- shadowed en bronze (FK estable)
    BusinessPartnerCategory       AS business_partner_category,
    BusinessPartnerFullName       AS full_name,               -- masked en bronze
    BusinessPartnerName           AS name,                    -- masked en bronze
    LegalForm                     AS legal_form,
    CAST(CreationDate AS DATE)    AS creation_date,
    CAST(LastChangeDate AS DATE)  AS last_change_date,
    load_date
FROM latest
ORDER BY business_partner
