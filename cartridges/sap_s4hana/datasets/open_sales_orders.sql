-- open_sales_orders  (gold)  cartridge: sap_s4hana
-- sources: ["raw/sap_s4hana/SalesOrder", "raw/sap_s4hana/SalesOrderItem", "silver/sap_s4hana/sap_s4hana_sales_orders_full"]
-- description: Backlog de pedidos de venta abiertos (no completados) por cliente: número, valor pendiente y antigüedad del más viejo.

-- OverallSDProcessStatus = 'C' significa completado; abierto = cualquier otro.
WITH so AS (
    SELECT sales_order, customer_code, sales_order_date, overall_status, net_amount, currency
    FROM read_parquet('s3://{bucket}/silver/sap_s4hana/sap_s4hana_sales_orders_full/**/*.parquet')
    WHERE overall_status IS DISTINCT FROM 'C'
)
SELECT
    customer_code                                                  AS customer_code,
    currency                                                       AS currency,
    COUNT(DISTINCT sales_order)                                    AS open_orders,
    ROUND(SUM(net_amount), 2)                                      AS open_value,
    MIN(sales_order_date)                                          AS oldest_order_date,
    CAST(DATE_DIFF('day', MIN(sales_order_date), CURRENT_DATE) AS INTEGER) AS oldest_age_days
FROM so
GROUP BY customer_code, currency
ORDER BY open_value DESC, customer_code
