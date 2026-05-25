-- sap_successfactors_folocation_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOLocation"]
-- description: Última extracción del objeto de fundación Ubicación (FOLocation).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOLocation/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOLocation/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    externalCode         AS location_id,
    name_defaultValue    AS location_name,
    country              AS country,
    load_date
FROM latest
ORDER BY location_id
