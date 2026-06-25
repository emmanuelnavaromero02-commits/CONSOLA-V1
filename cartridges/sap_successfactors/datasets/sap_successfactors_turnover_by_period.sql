-- sap_successfactors_turnover_by_period  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_empemploymenttermination_latest", "silver/sap_successfactors/sap_successfactors_foeventreason_latest"]
-- description: Rotacion de personal: bajas por mes y motivo desde EmpEmploymentTermination enriquecido con FOEventReason.

-- NOTA: EmpEmploymentTermination está registrado en entity_config por el seed de
-- completitud de SAP SuccessFactors; este gold queda vacío solo si el tenant no
-- trae bajas en la ventana extraída.
WITH term AS (
    SELECT user_id, termination_date, event_reason
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_empemploymenttermination_latest/**/*.parquet')
    WHERE termination_date IS NOT NULL
),
reasons AS (
    SELECT event_reason_id, event_reason_name, event_reason_category
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_foeventreason_latest/**/*.parquet',
                      hive_partitioning = true,
                      union_by_name = true)
)
SELECT
    CAST(DATE_TRUNC('month', termination_date) AS DATE) AS termination_month,
    COALESCE(reasons.event_reason_name, term.event_reason, '(sin motivo)') AS event_reason,
    COALESCE(reasons.event_reason_category, 'unclassified') AS event_reason_category,
    COUNT(DISTINCT user_id)                             AS terminations
FROM term
LEFT JOIN reasons ON reasons.event_reason_id = term.event_reason
GROUP BY DATE_TRUNC('month', termination_date), COALESCE(reasons.event_reason_name, term.event_reason, '(sin motivo)'), COALESCE(reasons.event_reason_category, 'unclassified')
ORDER BY termination_month DESC, terminations DESC
