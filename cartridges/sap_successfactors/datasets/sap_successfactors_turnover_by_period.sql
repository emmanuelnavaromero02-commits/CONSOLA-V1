-- sap_successfactors_turnover_by_period  (gold)  cartridge: sap_successfactors
-- sources: ["raw/sap_successfactors/EmpEmploymentTermination"]
-- description: Rotación de personal: bajas por mes y motivo desde EmpEmploymentTermination.

-- NOTA: EmpEmploymentTermination está registrado en entity_config por el seed de
-- completitud de SAP SuccessFactors; este gold queda vacío solo si el tenant no
-- trae bajas en la ventana extraída.
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
