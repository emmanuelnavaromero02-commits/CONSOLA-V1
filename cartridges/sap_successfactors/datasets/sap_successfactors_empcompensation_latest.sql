-- sap_successfactors_empcompensation_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpCompensation"]
-- description: Última extracción de EmpCompensation (cabecera de compensación: grupo de pago, frecuencia). userId plano.

-- NOTA: EmpCompensation no declara select_fields; campos SF estándar de cabecera.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpCompensation/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpCompensation/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    userId               AS user_id,            -- plano
    CAST(startDate AS DATE) AS start_date,
    payGroup             AS pay_group,
    frequencyCode        AS frequency_code,
    load_date
FROM latest
ORDER BY user_id, start_date
