-- sap_successfactors_empemployment_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmployment"]
-- description: Última extracción de EmpEmployment (relación laboral). Puente entre userId y personIdExternal (ambos planos).

WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmployment/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmployment/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    personIdExternal     AS person_id_external,   -- plano
    userId               AS user_id,              -- plano
    CAST(startDate AS DATE)         AS start_date,
    CAST(endDate AS DATE)           AS end_date,
    employeeClass        AS employee_class,
    CAST(originalStartDate AS DATE) AS original_start_date,
    load_date
FROM latest
ORDER BY user_id, start_date
