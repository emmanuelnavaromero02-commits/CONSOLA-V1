-- cost_center_expense  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/CostCenter", "raw/sap_s4hana/PurchaseOrder", "silver/sap_s4hana/sap_s4hana_costcenter_latest"]
-- description: Gasto por centro de costo. PENDIENTE: el enlace compra -> centro de costo vive en la asignación contable de la línea (no extraída); hoy se lista el maestro de centros de costo con expense en NULL.

-- TODO: el gasto real requiere A_PurchaseOrderAccountAssignment (CostCenter por
-- línea de OC), que no está en entities.yaml. Cuando se extraiga, unir por
-- cost_center y SUM(line_value) por mes.
WITH cc AS (
    SELECT cost_center, company_code, controlling_area, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_costcenter_latest/**/*.parquet')
)
SELECT
    cost_center                                     AS cost_center,
    company_code                                    AS company_code,
    controlling_area                                AS controlling_area,
    currency                                        AS currency,
    CAST(NULL AS DECIMAL(15,2))                     AS total_expense,   -- TODO: account assignment
    CAST(DATE_TRUNC('month', CURRENT_DATE) AS DATE) AS snapshot_month
FROM cc
ORDER BY company_code, cost_center
