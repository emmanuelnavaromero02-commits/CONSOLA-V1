-- absence_by_type_and_month  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/LeaveAbsence"]
-- description: Tendencia mensual de ausencias por tipo: total de días hábiles y empleados distintos afectados.

SELECT
    CAST(DATE_TRUNC('month', CAST(valid_from AS DATE)) AS DATE) AS absence_month,
    absence_type                                               AS absence_type,
    ROUND(SUM(absence_days_workable), 2)                       AS total_days_workable,
    COUNT(DISTINCT pernr)                                      AS employees_affected,
    COUNT(*)                                                   AS absence_records
FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_leaveabsence_latest/**/*.parquet')
WHERE valid_from IS NOT NULL
GROUP BY 1, 2
ORDER BY absence_month DESC, absence_type
