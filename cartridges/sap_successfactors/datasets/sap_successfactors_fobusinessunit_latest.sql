-- sap_successfactors_fobusinessunit_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOBusinessUnit"]
-- description: Última extracción del objeto de fundación Unidad de Negocio (FOBusinessUnit).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOBusinessUnit/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOBusinessUnit/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    externalCode         AS business_unit_id,
    name_defaultValue    AS business_unit_name,
    load_date
FROM latest
ORDER BY business_unit_id
