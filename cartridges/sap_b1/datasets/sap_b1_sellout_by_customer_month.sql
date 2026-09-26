-- sap_b1_sellout_by_customer_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_customer_crosswalk", "silver/sap_b1/sap_b1_business_partners"]
-- description: Sell-out per clinic: for every group company that buys from the group (distributor), what it sold to each external customer per month, in units and in its local_currency, net of the footer discount and of credit memos, with the gross margin, the number of items, the customer's channel (business partner group) and the customer identity shared across companies; the share of the distributor's sell-out that each customer represents; cancelled documents are excluded.
-- partition_by: period

WITH lines AS (
    SELECT company, doc_month, local_currency, card_code, card_name, is_intercompany, counterparty_company, item_code,
           COALESCE(quantity, 0) AS qty, amount_local_net AS amount, COALESCE(cost_local, 0) AS cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
    UNION ALL
    SELECT company, doc_month, local_currency, card_code, card_name, is_intercompany, counterparty_company, item_code,
           -COALESCE(quantity, 0), -amount_local_net, -COALESCE(cost_local, 0)
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
distributors AS (
    SELECT DISTINCT counterparty_company AS company
    FROM lines
    WHERE is_intercompany AND counterparty_company IS NOT NULL
),
per_customer AS (
    SELECT company, doc_month, local_currency, card_code,
           MAX(card_name)                                   AS card_name,
           SUM(qty)                                         AS units,
           SUM(amount)                                      AS revenue,
           SUM(amount - cost)                               AS gross_margin,
           COUNT(DISTINCT item_code) FILTER (WHERE item_code IS NOT NULL) AS items
    FROM lines
    WHERE NOT is_intercompany AND company IN (SELECT company FROM distributors)
    GROUP BY 1, 2, 3, 4
)
SELECT
    p.company                                                              AS distributor,
    p.doc_month,
    strftime(p.doc_month, '%Y-%m')                                         AS period,
    p.local_currency,
    p.card_code,
    p.card_name,
    COALESCE(x.customer_key, p.company || ':' || p.card_code)              AS customer_key,
    COALESCE(bp.group_name, 'Sin grupo')                                   AS channel_name,
    ROUND(p.units, 6)                                                      AS units,
    ROUND(p.revenue, 2)                                                    AS sell_out_revenue_local,
    ROUND(p.gross_margin, 2)                                               AS sell_out_gross_margin_local,
    CASE WHEN p.revenue <> 0 THEN ROUND(100.0 * p.gross_margin / p.revenue, 2) END AS margin_pct,
    p.items,
    ROUND(100.0 * p.revenue / NULLIF(SUM(p.revenue) OVER (PARTITION BY p.company, p.doc_month), 0), 4) AS share_of_sell_out_pct,
    ROW_NUMBER() OVER (PARTITION BY p.company, p.doc_month ORDER BY p.revenue DESC, p.card_code) AS rank_in_month
FROM per_customer p
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_customer_crosswalk/**/*.parquet') x
  ON x.company = p.company AND x.card_code = p.card_code
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_partners/**/*.parquet') bp
  ON bp.company = p.company AND bp.card_code = p.card_code
ORDER BY distributor, p.doc_month, rank_in_month
