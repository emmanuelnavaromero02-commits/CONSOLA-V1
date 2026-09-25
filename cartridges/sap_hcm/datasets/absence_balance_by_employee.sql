-- absence_balance_by_employee  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/LeaveAbsence", "silver/sap_hcm/sap_hcm_leaveabsence_latest"]
-- description: Días de ausencia por empleado y tipo en los últimos 12 meses. Una fila por (pernr, tipo de ausencia).

WITH absences AS (
    -- Ventana móvil de 12 meses.
    SELECT pernr, absence_type, absence_days_workable, absence_days_calendar
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_leaveabsence_latest/**/*.parquet')
    WHERE CAST(valid_from AS DATE) >= (CURRENT_DATE - INTERVAL 12 MONTH)
)
SELECT
    pernr                                          AS pernr,          -- shadowed (FK)
    absence_type                                   AS absence_type,
    ROUND(SUM(absence_days_workable), 2)           AS total_days_workable,
    ROUND(SUM(absence_days_calendar), 2)           AS total_days_calendar,
    COUNT(*)                                       AS absence_records
FROM absences
GROUP BY pernr, absence_type
ORDER BY pernr, absence_type
