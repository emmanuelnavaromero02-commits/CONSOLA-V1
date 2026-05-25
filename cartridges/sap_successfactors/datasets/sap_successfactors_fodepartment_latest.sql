-- sap_successfactors_fodepartment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/FODepartment"]
-- description: Última extracción del objeto de fundación Departamento (FODepartment).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODepartment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/FODepartment/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    externalCode         AS department_id,
    name_defaultValue    AS department_name,
    costCenter           AS cost_center,
    load_date
FROM latest
ORDER BY department_id
