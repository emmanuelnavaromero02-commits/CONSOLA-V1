-- sap_hcm_employeemaster_latest  (silver)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster"]
-- description: Última extracción de EmployeeMaster (PA0001) con campos tipados, filtrada por el load_date más reciente.

WITH latest AS (
    -- Solo la última extracción (snapshot más reciente en bronze).
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeMaster/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_hcm/EmployeeMaster/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    Pernr                 AS pernr,            -- shadowed en bronze: hash estable, usado como FK
    CAST(Begda AS DATE)   AS valid_from,
    CAST(Endda AS DATE)   AS valid_to,
    Bukrs                 AS company_code,
    Werks                 AS personnel_area,
    Persg                 AS employee_group,
    Persk                 AS employee_subgroup,
    Orgeh                 AS org_unit_id,
    Plans                 AS position_id,
    Kostl                 AS cost_center,
    AedtmAed              AS changed_on,
    load_date
FROM latest
ORDER BY pernr, valid_from
