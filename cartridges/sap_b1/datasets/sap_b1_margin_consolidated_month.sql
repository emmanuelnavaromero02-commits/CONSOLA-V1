-- sap_b1_margin_consolidated_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_inventory_movements", "silver/sap_b1/sap_b1_stock_on_hand"]
-- description: Group gross margin per month: external sales of every company, plus the profit of sales between group companies, minus the change of the profit still unrealized in the buying company (units bought from the group and not yet sold outside it, starting from the stock back-cast to the month before the first sale, times the average intercompany unit profit of that buyer and item); cancelled documents are excluded.

WITH invoices AS (
    SELECT company, doc_month, doc_date, local_currency, is_intercompany, counterparty_company, item_code,
           quantity, amount_local_net, COALESCE(cost_local, 0) AS cost_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month, local_currency, is_intercompany, amount_local_net, COALESCE(cost_local, 0) AS cost_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
sales AS (
    SELECT doc_month, local_currency, is_intercompany, amount_local_net AS revenue, cost_local AS cost FROM invoices
    UNION ALL
    SELECT doc_month, local_currency, is_intercompany, -amount_local_net, -cost_local FROM credits
),
monthly AS (
    SELECT doc_month, local_currency,
           SUM(CASE WHEN NOT is_intercompany THEN revenue ELSE 0 END) AS external_revenue,
           SUM(CASE WHEN NOT is_intercompany THEN cost ELSE 0 END)    AS external_cost,
           SUM(CASE WHEN is_intercompany THEN revenue ELSE 0 END)     AS intercompany_revenue,
           SUM(CASE WHEN is_intercompany THEN cost ELSE 0 END)        AS intercompany_cost
    FROM sales
    GROUP BY 1, 2
),
pairs AS (
    SELECT DISTINCT counterparty_company AS buyer, item_code, local_currency
    FROM invoices
    WHERE is_intercompany AND counterparty_company IS NOT NULL AND item_code IS NOT NULL
),
unit_profit AS (
    SELECT counterparty_company AS buyer, item_code, local_currency,
           SUM(amount_local_net - cost_local) / NULLIF(SUM(quantity), 0) AS unit_profit
    FROM invoices
    WHERE is_intercompany AND counterparty_company IS NOT NULL AND item_code IS NOT NULL
    GROUP BY 1, 2, 3
),
item_credits AS (
    SELECT company, doc_month, local_currency, is_intercompany, counterparty_company, item_code, COALESCE(quantity, 0) AS quantity
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N' AND item_code IS NOT NULL
),
flows AS (
    SELECT counterparty_company AS buyer, item_code, local_currency, doc_month AS m, quantity AS qty
    FROM invoices WHERE is_intercompany AND counterparty_company IS NOT NULL AND item_code IS NOT NULL
    UNION ALL
    SELECT counterparty_company, item_code, local_currency, doc_month, -quantity
    FROM item_credits WHERE is_intercompany AND counterparty_company IS NOT NULL
    UNION ALL
    SELECT company, item_code, local_currency, doc_month, -quantity
    FROM invoices WHERE NOT is_intercompany AND item_code IS NOT NULL
    UNION ALL
    SELECT company, item_code, local_currency, doc_month, quantity
    FROM item_credits WHERE NOT is_intercompany
),
anchor AS (
    SELECT CAST(MIN(doc_month) - INTERVAL 1 MONTH AS DATE) AS anchor_month FROM monthly
),
points AS (
    SELECT DISTINCT doc_month AS point_month FROM monthly
    UNION
    SELECT anchor_month FROM anchor
),
stock_now AS (
    SELECT company, item_code, SUM(on_hand) AS on_hand
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
    GROUP BY 1, 2
),
move_month AS (
    SELECT company, item_code, CAST(DATE_TRUNC('month', doc_date) AS DATE) AS m, SUM(net_qty) AS net
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_inventory_movements/**/*.parquet')
    GROUP BY 1, 2, 3
),
opening_stock AS (
    SELECT pr.buyer, pr.item_code, pr.local_currency,
           COALESCE(ANY_VALUE(s.on_hand), 0)
             - COALESCE(SUM(CASE WHEN mm.m > a.anchor_month THEN mm.net ELSE 0 END), 0) AS qty
    FROM pairs pr
    CROSS JOIN anchor a
    LEFT JOIN stock_now s ON s.company = pr.buyer AND s.item_code = pr.item_code
    LEFT JOIN move_month mm ON mm.company = pr.buyer AND mm.item_code = pr.item_code
    GROUP BY 1, 2, 3
),
unsold_at AS (
    SELECT p.point_month, o.buyer, o.item_code, o.local_currency,
           o.qty + COALESCE(SUM(CASE WHEN f.m <= p.point_month THEN f.qty ELSE 0 END), 0) AS qty
    FROM points p
    CROSS JOIN opening_stock o
    LEFT JOIN flows f ON f.buyer = o.buyer AND f.item_code = o.item_code AND f.local_currency = o.local_currency
    GROUP BY 1, 2, 3, 4, o.qty
),
unrealized AS (
    SELECT u.point_month, u.local_currency,
           CAST(SUM(GREATEST(u.qty, 0) * COALESCE(k.unit_profit, 0)) AS DECIMAL(19,6)) AS unrealized_profit
    FROM unsold_at u
    LEFT JOIN unit_profit k ON k.buyer = u.buyer AND k.item_code = u.item_code AND k.local_currency = u.local_currency
    GROUP BY 1, 2
)
SELECT
    m.doc_month,
    m.local_currency,
    ROUND(m.external_revenue, 2)                                              AS external_revenue_net_local,
    ROUND(m.external_cost, 2)                                                 AS external_cost_net_local,
    ROUND(m.external_revenue - m.external_cost, 2)                            AS external_gross_profit_local,
    ROUND(m.intercompany_revenue, 2)                                          AS intercompany_revenue_net_local,
    ROUND(m.intercompany_revenue - m.intercompany_cost, 2)                    AS intercompany_gross_profit_local,
    ROUND(COALESCE(uo.unrealized_profit, 0), 2)                               AS unrealized_profit_open_local,
    ROUND(COALESCE(uc.unrealized_profit, 0), 2)                               AS unrealized_profit_close_local,
    ROUND(m.external_revenue - m.external_cost + m.intercompany_revenue - m.intercompany_cost
          - (COALESCE(uc.unrealized_profit, 0) - COALESCE(uo.unrealized_profit, 0)), 2) AS consolidated_gross_profit_local,
    CASE WHEN m.external_revenue <> 0 THEN ROUND(100.0 * (m.external_revenue - m.external_cost) / m.external_revenue, 2) END
                                                                              AS external_margin_pct,
    CASE WHEN m.external_revenue <> 0 THEN ROUND(100.0 * (
            m.external_revenue - m.external_cost + m.intercompany_revenue - m.intercompany_cost
            - (COALESCE(uc.unrealized_profit, 0) - COALESCE(uo.unrealized_profit, 0))
         ) / m.external_revenue, 2) END                                       AS consolidated_margin_pct
FROM monthly m
LEFT JOIN unrealized uc ON uc.point_month = m.doc_month AND uc.local_currency = m.local_currency
LEFT JOIN unrealized uo ON uo.point_month = CAST(m.doc_month - INTERVAL 1 MONTH AS DATE) AND uo.local_currency = m.local_currency
ORDER BY m.doc_month, m.local_currency
