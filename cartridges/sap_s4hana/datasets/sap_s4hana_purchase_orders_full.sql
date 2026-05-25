-- sap_s4hana_purchase_orders_full  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/PurchaseOrder", "raw/sap_s4hana/PurchaseOrderItem"]
-- description: Órdenes de compra a nivel línea, enriquecidas con la cabecera (proveedor, fecha, sociedad, moneda). Una fila por línea.

-- NOTA de privacidad: PurchaseOrder.supplier va en claro (A_PurchaseOrder no tiene
-- regla de protección); no se une al maestro de proveedores (Supplier.supplier
-- está shadowed). El código de proveedor se conserva como dimensión.
WITH orders AS (
    SELECT purchase_order, purchase_order_type, supplier, company_code,
           purchase_order_date, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_purchaseorder_latest/**/*.parquet')
),
items AS (
    SELECT purchase_order, purchase_order_item, material, order_quantity, net_price_amount, plant
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_purchaseorderitem_latest/**/*.parquet')
)
SELECT
    o.purchase_order         AS purchase_order,
    i.purchase_order_item    AS purchase_order_item,
    o.purchase_order_type    AS purchase_order_type,
    o.supplier               AS supplier_code,
    o.company_code           AS company_code,
    o.purchase_order_date    AS purchase_order_date,
    i.material               AS material,
    i.order_quantity         AS order_quantity,
    i.net_price_amount       AS net_price_amount,
    ROUND(COALESCE(i.order_quantity, 0) * COALESCE(i.net_price_amount, 0), 2) AS line_value,
    o.currency               AS currency,
    i.plant                  AS plant
FROM orders o
LEFT JOIN items i ON i.purchase_order = o.purchase_order
ORDER BY o.purchase_order, i.purchase_order_item
