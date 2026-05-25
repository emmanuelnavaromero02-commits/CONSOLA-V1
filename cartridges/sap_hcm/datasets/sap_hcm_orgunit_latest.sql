-- sap_hcm_orgunit_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/OrgUnit"]
-- description: Última extracción de OrgUnit (HRP1000 Otype=O): unidades organizacionales con id y nombre.

-- HRP1000 estándar: Objid = id del objeto OM, Stext = texto largo, Short = texto corto.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/OrgUnit/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/OrgUnit/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Objid                AS org_unit_id,
    Stext                AS org_unit_name,
    Short                AS org_unit_short,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    load_date
FROM latest
ORDER BY org_unit_id, valid_from
