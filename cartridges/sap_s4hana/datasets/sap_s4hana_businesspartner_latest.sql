-- sap_s4hana_businesspartner_latest  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/BusinessPartner"]
-- description: Última extracción de Business Partners (maestro) con campos tipados. El id y el nombre llegan ya protegidos desde bronze (shadowed / masked).

WITH latest AS (
    -- Estado ACTUAL por clave de negocio sobre TODO el historico bronze.
    -- BusinessPartner es incremental: cada load_date trae solo los cambios desde el
    -- watermark, asi que quedarse con la ultima particion (MAX(load_date))
    -- colapsaba la poblacion al delta del dia — perdida silenciosa de datos.
    -- Dedupe determinista: la ultima version de cada fila (BusinessPartner).
    -- Limite conocido: un borrado fisico en la fuente no se refleja hasta un
    -- full load (el incremental OData no acarrea deletes).
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY BusinessPartner
                   ORDER BY load_date DESC, LastChangeDate DESC NULLS LAST
               ) AS _rn
        FROM read_parquet('s3://{bucket}/raw/sap_s4hana/BusinessPartner/**/*.parquet',
                          hive_partitioning = true,
                          union_by_name   = true)
    )
    WHERE _rn = 1
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
