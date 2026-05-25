-- sap_successfactors_empjob_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpJob"]
-- description: Última extracción de EmpJob (asignación de puesto efectivo-fechada). userId y managerId planos; códigos de org casan con externalCode de los FO.

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpJob/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpJob/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    userId               AS user_id,              -- plano
    CAST(startDate AS DATE) AS start_date,
    CAST(endDate AS DATE)   AS end_date,
    jobCode              AS job_code,
    position             AS position,
    department           AS department,
    division             AS division,
    location             AS location,
    businessUnit         AS business_unit,
    company              AS company,
    costCenter           AS cost_center,
    managerId            AS manager_id,           -- plano (habilita manager_hierarchy real)
    eventReason          AS event_reason,
    load_date
FROM latest
ORDER BY user_id, start_date
