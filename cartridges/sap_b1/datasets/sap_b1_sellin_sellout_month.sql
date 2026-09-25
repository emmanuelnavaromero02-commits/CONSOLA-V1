-- sap_b1_sellin_sellout_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_item_crosswalk", "silver/sap_b1/sap_b1_inventory_movements", "silver/sap_b1/sap_b1_stock_on_hand"]
-- description: Sell-in and sell-out per buying group company (distributor), item and month: units and value the group sold to it, units, revenue and margin it sold to external customers, and its month-end stock back-cast from today's stock and later movements; items are matched across companies by their shared identity; cancelled documents are excluded.

WITH items AS (
    SELECT company, item_code, item_key
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet')
),
lines AS (
    SELECT company, doc_month, local_currency, is_intercompany, counterparty_company, item_code,
           COALESCE(quantity, 0) AS qty, amount_local AS amount, COALESCE(cost_local, 0) AS cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N' AND item_code IS NOT NULL
    UNION ALL
    SELECT company, doc_month, local_currency, is_intercompany, counterparty_company, item_code,
           -COALESCE(quantity, 0), -amount_local, -COALESCE(cost_local, 0)
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N' AND item_code IS NOT NULL
),
distributors AS (
    SELECT DISTINCT counterparty_company AS company
    FROM lines
    WHERE is_intercompany AND counterparty_company IS NOT NULL
),
sell_in AS (
    SELECT l.counterparty_company AS distributor, l.doc_month, l.local_currency,
           COALESCE(i.item_key, 'CODE:' || l.item_code) AS item_key,
           SUM(l.qty) AS sell_in_qty, SUM(l.amount) AS sell_in_amount
    FROM lines l
    LEFT JOIN items i ON i.company = l.company AND i.item_code = l.item_code
    WHERE l.is_intercompany AND l.counterparty_company IN (SELECT company FROM distributors)
    GROUP BY 1, 2, 3, 4
),
sell_out AS (
    SELECT l.company AS distributor, l.doc_month, l.local_currency,
           COALESCE(i.item_key, 'CODE:' || l.item_code) AS item_key,
           SUM(l.qty) AS sell_out_qty, SUM(l.amount) AS sell_out_revenue, SUM(l.amount - l.cost) AS sell_out_gross_profit
    FROM lines l
    LEFT JOIN items i ON i.company = l.company AND i.item_code = l.item_code
    WHERE NOT l.is_intercompany AND l.company IN (SELECT company FROM distributors)
    GROUP BY 1, 2, 3, 4
),
flows AS (
    SELECT COALESCE(i.distributor, o.distributor) AS distributor,
           COALESCE(i.doc_month, o.doc_month) AS doc_month,
           COALESCE(i.local_currency, o.local_currency) AS local_currency,
           COALESCE(i.item_key, o.item_key) AS item_key,
           COALESCE(i.sell_in_qty, 0) AS sell_in_qty, COALESCE(i.sell_in_amount, 0) AS sell_in_amount,
           COALESCE(o.sell_out_qty, 0) AS sell_out_qty, COALESCE(o.sell_out_revenue, 0) AS sell_out_revenue,
           COALESCE(o.sell_out_gross_profit, 0) AS sell_out_gross_profit
    FROM sell_in i
    FULL OUTER JOIN sell_out o
      ON o.distributor = i.distributor AND o.doc_month = i.doc_month
     AND o.local_currency = i.local_currency AND o.item_key = i.item_key
),
stock_now AS (
    SELECT s.company, COALESCE(i.item_key, 'CODE:' || s.item_code) AS item_key, SUM(s.on_hand) AS on_hand
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet') s
    LEFT JOIN items i ON i.company = s.company AND i.item_code = s.item_code
    WHERE s.company IN (SELECT company FROM distributors)
    GROUP BY 1, 2
),
moves AS (
    SELECT m.company, COALESCE(i.item_key, 'CODE:' || m.item_code) AS item_key,
           CAST(DATE_TRUNC('month', m.doc_date) AS DATE) AS m, SUM(m.net_qty) AS net
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_inventory_movements/**/*.parquet') m
    LEFT JOIN items i ON i.company = m.company AND i.item_code = m.item_code
    WHERE m.company IN (SELECT company FROM distributors)
    GROUP BY 1, 2, 3
),
month_end_stock AS (
    SELECT f.distributor, f.doc_month, f.item_key,
           COALESCE(ANY_VALUE(s.on_hand), 0) - COALESCE(SUM(CASE WHEN mv.m > f.doc_month THEN mv.net ELSE 0 END), 0) AS stock_end_qty
    FROM (SELECT DISTINCT distributor, doc_month, item_key FROM flows) f
    LEFT JOIN stock_now s ON s.company = f.distributor AND s.item_key = f.item_key
    LEFT JOIN moves mv ON mv.company = f.distributor AND mv.item_key = f.item_key
    GROUP BY 1, 2, 3
)
SELECT
    f.distributor,
    f.doc_month,
    strftime(f.doc_month, '%Y-%m')                              AS period,
    f.local_currency,
    f.item_key,
    ROUND(f.sell_in_qty, 6)                                     AS sell_in_qty,
    ROUND(f.sell_in_amount, 2)                                  AS sell_in_amount_local,
    ROUND(f.sell_out_qty, 6)                                    AS sell_out_qty,
    ROUND(f.sell_out_revenue, 2)                                AS sell_out_revenue_local,
    ROUND(f.sell_out_gross_profit, 2)                           AS sell_out_gross_profit_local,
    ROUND(s.stock_end_qty, 6)                                   AS stock_end_qty
FROM flows f
LEFT JOIN month_end_stock s
  ON s.distributor = f.distributor AND s.doc_month = f.doc_month AND s.item_key = f.item_key
ORDER BY f.distributor, f.doc_month, f.item_key
