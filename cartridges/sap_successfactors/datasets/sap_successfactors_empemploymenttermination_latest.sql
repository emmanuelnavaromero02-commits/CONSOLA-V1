-- sap_successfactors_empemploymenttermination_latest  (silver)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Última extracción de bajas (EmpEmploymentTermination). userId plano. Puede venir vacío si la entidad aún no está habilitada para extracción.

-- NOTA: EmpEmploymentTermination está en entities.yaml pero no en el seed de
-- entity_config (extracción no habilitada en Bloque A); nombres SF estándar.
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
