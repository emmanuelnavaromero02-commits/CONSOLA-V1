-- headcount_by_department  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "raw/sap_hcm/OrgUnit"]
-- description: Empleados activos por unidad organizacional (snapshot del mes en curso).

WITH emp AS (
    -- Empleados vigentes desde la vista 360.
    SELECT org_unit_id, org_unit_name
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    org_unit_id                              AS org_id,
    COALESCE(org_unit_name, '(sin nombre)')  AS org_name,
    COUNT(*)                                 AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY org_unit_id, org_unit_name
ORDER BY headcount DESC, org_id
