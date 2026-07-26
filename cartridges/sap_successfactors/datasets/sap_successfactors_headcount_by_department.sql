-- sap_successfactors_headcount_by_department  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Empleados activos por departamento (snapshot del mes en curso).

-- Nombre prefijado con el cartucho: datasets.name es PK global y headcount_by_department
-- ya existe para sap_hcm.
WITH emp AS (
    SELECT department_id, department_name
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    department_id,
    department_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
WHERE NULLIF(TRIM(department_name), '') IS NOT NULL
  AND LOWER(TRIM(department_name)) <> '(sin nombre)'
GROUP BY department_id, department_name
ORDER BY headcount DESC, department_name
