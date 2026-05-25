-- sap_s4hana_sales_orders_full  (silver)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SalesOrder", "raw/sap_s4hana/SalesOrderItem"]
-- description: Pedidos de venta a nivel línea, enriquecidos con la cabecera (cliente, fecha, estado, moneda). Una fila por línea.

-- NOTA de privacidad: no se une al maestro de clientes porque Customer.customer
-- está shadowed (hash) mientras SalesOrder.sold_to_party va en claro (A_SalesOrder
-- no tiene regla de protección); el código de cliente se conserva como dimensión.
WITH orders AS (
    SELECT sales_order, sales_order_type, sold_to_party, sales_organization,
           sales_order_date, overall_status, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_salesorder_latest/**/*.parquet')
),
items AS (
    SELECT sales_order, sales_order_item, material, requested_quantity, net_amount, plant
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_salesorderitem_latest/**/*.parquet')
)
SELECT
    o.sales_order            AS sales_order,
    i.sales_order_item       AS sales_order_item,
    o.sales_order_type       AS sales_order_type,
    o.sold_to_party          AS customer_code,
    o.sales_organization     AS sales_organization,
    o.sales_order_date       AS sales_order_date,
    o.overall_status         AS overall_status,
    i.material               AS material,
    i.requested_quantity     AS requested_quantity,
    i.net_amount             AS net_amount,
    o.currency               AS currency,
    i.plant                  AS plant
FROM orders o
LEFT JOIN items i ON i.sales_order = o.sales_order
ORDER BY o.sales_order, i.sales_order_item
