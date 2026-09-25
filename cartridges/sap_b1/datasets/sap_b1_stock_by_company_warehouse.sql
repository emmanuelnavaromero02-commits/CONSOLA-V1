-- sap_b1_stock_by_company_warehouse  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_stock_on_hand"]
-- description: Stock position per company and warehouse from the newest snapshot: items with stock, quantities on hand, committed and on order, and the stock value at average price in local currency.

SELECT
    company,
    warehouse,
    warehouse_name,
    local_currency,
    COUNT(*)                                            AS item_warehouse_rows,
    COUNT(CASE WHEN on_hand > 0 THEN 1 END)             AS items_with_stock,
    ROUND(SUM(on_hand), 6)                              AS on_hand_qty,
    ROUND(SUM(COALESCE(committed, 0)), 6)               AS committed_qty,
    ROUND(SUM(COALESCE(on_order, 0)), 6)                AS on_order_qty,
    ROUND(SUM(stock_value_local), 2)                    AS stock_value_local,
    MAX(load_date)                                      AS snapshot_load_date
FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
GROUP BY 1, 2, 3, 4
ORDER BY company, warehouse
