-- headcount_by_position_type  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/PersonalData", "raw/sap_hcm/ContractData", "silver/sap_hcm/sap_hcm_employee_master_full"]
-- description: Distribución de empleados activos por tipo de personal (employee_group / subgroup de PA0001). Proxy de "tipo de posición" hasta que se extraiga la clasificación de puesto.

-- NOTA: PA0001 extraído no trae el código de puesto (Stell); como proxy de tipo
-- se usa la clasificación de personal employee_group (Persg) + subgroup (Persk).
WITH emp AS (
    SELECT employee_group, employee_subgroup
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(employee_group, '(sin grupo)')         AS employee_group,
    COALESCE(employee_subgroup, '(sin subgrupo)')   AS employee_subgroup,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY employee_group, employee_subgroup
ORDER BY headcount DESC, employee_group, employee_subgroup
