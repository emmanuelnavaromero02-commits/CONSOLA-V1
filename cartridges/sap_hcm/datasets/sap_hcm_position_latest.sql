-- sap_hcm_position_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/Position"]
-- description: Última extracción de Position (HRP1000 Otype=S): posiciones con id y nombre.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/Position/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/Position/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Objid                AS position_id,
    Stext                AS position_name,
    Short                AS position_short,
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    load_date
FROM latest
ORDER BY position_id, valid_from
