-- workforce_cost_monthly  (gold)  cartridge: sap_hcm
-- sources: ["raw/sap_hcm/EmployeeMaster", "raw/sap_hcm/OrgUnit"]
-- description: Costo de nómina estimado por mes / unidad org / centro de costo. PENDIENTE: requiere extracción de PA0008 (BasicPay), no habilitada en Bloque A. Hoy entrega la dimensión con headcount y costo en NULL.

-- TODO: requiere extracción de PA0008 (BasicPay) que aún no está habilitada.
-- Cuando exista raw/sap_hcm/BasicPay (PA0008.Betrg = importe, Lga = clase de
-- pago), unir por pernr y rango de fechas y reemplazar el NULL por:
--   ROUND(SUM(CAST(bp.Betrg AS DECIMAL(14,2))), 2) AS total_base_salary
-- agregando por mes/org/centro de costo.
WITH emp AS (
    SELECT org_unit_id, org_unit_name, cost_center
    FROM read_parquet('s3://{bucket}/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet')
    WHERE is_active = TRUE
)
SELECT
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS cost_month,
    org_unit_id                                     AS org_unit_id,
    COALESCE(org_unit_name, '(sin nombre)')         AS org_unit_name,
    COALESCE(cost_center, '(sin centro)')           AS cost_center,
    COUNT(*)                                        AS active_headcount,
    CAST(NULL AS DECIMAL(14,2))                     AS total_base_salary  -- TODO: PA0008
FROM emp
GROUP BY org_unit_id, org_unit_name, cost_center
ORDER BY org_unit_id, cost_center
