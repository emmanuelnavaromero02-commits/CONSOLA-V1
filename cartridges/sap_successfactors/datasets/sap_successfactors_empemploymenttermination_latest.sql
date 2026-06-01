-- sap_successfactors_empemploymenttermination_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Última extracción de bajas (EmpEmploymentTermination). userId plano; la entidad está registrada para extracción.

-- NOTA: EmpEmploymentTermination está registrado en entity_config por el seed de
-- completitud de SAP SuccessFactors; nombres SF estándar.
WITH latest AS (
    SELECT *
    FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmploymentTermination/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name   = true)
    WHERE load_date = (SELECT MAX(load_date)
                       FROM read_parquet('s3://{bucket}/raw/sap_successfactors/EmpEmploymentTermination/**/*.parquet',
                                          hive_partitioning = true))
)
SELECT
    userId                     AS user_id,            -- plano
    CAST(endDate AS DATE)      AS termination_date,
    eventReasonExternalCode    AS event_reason,
    load_date
FROM latest
ORDER BY user_id, termination_date
