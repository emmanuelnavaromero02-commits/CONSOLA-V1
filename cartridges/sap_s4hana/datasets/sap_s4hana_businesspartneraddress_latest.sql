-- sap_s4hana_businesspartneraddress_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BusinessPartnerAddress"]
-- description: Última extracción de direcciones de Business Partner. Calle/número llegan masked; localidad/país en claro.

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- BusinessPartnerAddress es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (BusinessPartner, AddressID).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY BusinessPartner, AddressID
                   ORDER BY load_date DESC, LastChangeDate DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartnerAddress/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
