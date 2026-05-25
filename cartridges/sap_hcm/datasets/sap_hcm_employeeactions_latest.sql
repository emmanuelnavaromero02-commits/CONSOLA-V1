-- sap_hcm_employeeactions_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeActions"]
-- description: Última extracción de EmployeeActions (PA0000): altas, bajas y traslados, con campos tipados.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeActions/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeActions/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                AS pernr,           -- shadowed en bronze
    CAST(Begda AS DATE)  AS valid_from,
    CAST(Endda AS DATE)  AS valid_to,
    Massn                AS action_type,     -- p.ej. 01=alta, 02=baja, 04=traslado
    Massg                AS action_reason,
    AedtmAed             AS changed_on,
    load_date
FROM latest
ORDER BY pernr, valid_from
