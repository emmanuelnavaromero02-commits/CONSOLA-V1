-- sap_hcm_costcenter_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/CostCenter"]
-- description: Última extracción de CostCenter (subconjunto de PA0001 con Kostl) con campos tipados.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/CostCenter/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/CostCenter/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    Kostl                AS cost_center,
    Orgeh                AS org_unit_id,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY cost_center, pernr
