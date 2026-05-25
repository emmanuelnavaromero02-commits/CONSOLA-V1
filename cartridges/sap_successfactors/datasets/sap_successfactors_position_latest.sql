-- sap_successfactors_position_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/Position"]
-- description: Última extracción de Position Management (posiciones).

-- NOTA: Position no declara select_fields; campos SF estándar (code, nombre, org).
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Position/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/Position/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    code                       AS position_id,
    externalName_defaultValue  AS position_name,
    department                 AS department,
    location                   AS location,
    costCenter                 AS cost_center,
    load_date
FROM latest
ORDER BY position_id
