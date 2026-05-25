-- sap_successfactors_fodivision_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FODivision"]
-- description: Última extracción del objeto de fundación División (FODivision).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODivision/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODivision/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    externalCode         AS division_id,
    name_defaultValue    AS division_name,
    load_date
FROM latest
ORDER BY division_id
