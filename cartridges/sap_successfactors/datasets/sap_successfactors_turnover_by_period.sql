-- sap_successfactors_turnover_by_period  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Rotación de personal: bajas por mes y motivo. Puede venir vacío hasta habilitar la extracción de EmpEmploymentTermination.

-- NOTA: EmpEmploymentTermination está en entities.yaml pero su extracción no está
-- habilitada en entity_config (Bloque A); este gold se llena cuando lo esté.
WITH term AS (
    SELECT user_id, termination_date, event_reason
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empemploymenttermination_latest/**/*.parquet')
    WHERE termination_date IS NOT NULL
)
SELECT
    CAST(DATE_TRUNC('month', termination_date) AS DATE) AS termination_month,
    COALESCE(event_reason, '(sin motivo)')              AS event_reason,
    COUNT(DISTINCT user_id)                             AS terminations
FROM term
GROUP BY DATE_TRUNC('month', termination_date), event_reason
ORDER BY termination_month DESC, terminations DESC
