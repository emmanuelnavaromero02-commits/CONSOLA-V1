-- purchase_spend_by_supplier  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/PurchaseOrder", "raw/sap_s4hana/PurchaseOrderItem"]
-- description: Gasto de compras por proveedor y mes (cantidad x precio de línea). Ranking de proveedores por gasto.

WITH po AS (
    SELECT supplier_code, purchase_order, purchase_order_date, line_value, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_purchase_orders_full/**/*.parquet')
    WHERE purchase_order_date IS NOT NULL
)
SELECT
    supplier_code                                              AS supplier_code,
    CAST(DATE_TRUNC('month', purchase_order_date) AS DATE)     AS spend_month,
    currency                                                   AS currency,
    ROUND(SUM(line_value), 2)                                  AS total_spend,
    COUNT(DISTINCT purchase_order)                             AS po_count
FROM po
GROUP BY supplier_code, DATE_TRUNC('month', purchase_order_date), currency
ORDER BY spend_month DESC, total_spend DESC
