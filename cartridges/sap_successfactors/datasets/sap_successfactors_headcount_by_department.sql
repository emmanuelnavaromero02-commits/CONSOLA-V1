-- sap_successfactors_headcount_by_department  (gold)  cartridge: sap_successfactors
-- sources: ["silver/sap_successfactors/sap_successfactors_employee_360"]
-- description: Empleados activos por departamento (snapshot del mes en curso).

-- Nombre prefijado con el cartucho: datasets.name es PK global y headcount_by_department
-- ya existe para sap_hcm.
WITH emp AS (
    SELECT department_id, department_name
    FROM read_parquet('s3://{bucket}/silver/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    COALESCE(department_id, '(sin departamento)')   AS department_id,
    COALESCE(department_name, '(sin nombre)')       AS department_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
GROUP BY department_id, department_name
ORDER BY headcount DESC, department_id
