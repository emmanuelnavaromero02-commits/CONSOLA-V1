-- sap_successfactors_headcount_by_company  (gold)  cartridge: sap_successfactors
-- sources: ["gold/sap_successfactors/sap_successfactors_employee_360"]
-- description: Empleados activos por compañía legal (snapshot del mes en curso).

WITH emp AS (
    SELECT company_id, company_name
    FROM read_parquet('s3://{bucket}/gold/sap_successfactors/sap_successfactors_employee_360/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    company_id,
    company_name,
    COUNT(*)                                        AS headcount,
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM emp
WHERE NULLIF(TRIM(company_name), '') IS NOT NULL
  AND LOWER(TRIM(company_name)) <> '(sin nombre)'
GROUP BY company_id, company_name
ORDER BY headcount DESC, company_name
