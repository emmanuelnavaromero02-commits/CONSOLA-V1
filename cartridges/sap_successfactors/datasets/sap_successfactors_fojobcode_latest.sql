-- sap_successfactors_fojobcode_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FOJobCode"]
-- description: Última extracción del objeto de fundación Código de Puesto (FOJobCode).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOJobCode/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FOJobCode/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    externalCode         AS job_code,
    name_defaultValue    AS job_name,
    load_date
FROM latest
ORDER BY job_code
