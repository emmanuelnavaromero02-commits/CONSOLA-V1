-- headcount_by_costcenter  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData"]
-- description: Empleados activos por centro de costo (snapshot del mes en curso).

WITH emp AS (
    SELECT cost_center
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(cost_center, '(sin centro)')           AS cost_center,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY cost_center
ORDER BY headcount DESC, cost_center
