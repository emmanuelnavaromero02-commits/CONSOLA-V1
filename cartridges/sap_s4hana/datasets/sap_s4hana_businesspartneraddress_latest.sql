-- sap_s4hana_businesspartneraddress_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BusinessPartnerAddress"]
-- description: Última extracción de direcciones de Business Partner. Calle/número llegan masked; localidad/país en claro.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartnerAddress/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartnerAddress/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    BusinessPartner               AS business_partner,        -- shadowed en bronze (FK)
    AddressID                     AS address_id,
    StreetName                    AS street_name,             -- masked en bronze
    HouseNumber                   AS house_number,            -- masked en bronze
    PostalCode                    AS postal_code,
    CityName                      AS city_name,
    Country                       AS country,
    Region                        AS region,
    load_date
FROM latest
ORDER BY business_partner, address_id
