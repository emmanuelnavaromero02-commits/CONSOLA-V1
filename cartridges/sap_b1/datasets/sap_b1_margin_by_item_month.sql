-- sap_b1_margin_by_item_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_items", "silver/sap_b1/sap_b1_item_crosswalk", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Net margin per company, month and item with its item group and the item identity shared across companies; below_min_revenue and negative_margin_revenue add up the invoice lines of external sales under the minimum margin (threshold margin_min_pct) or sold below cost; lines without an item are grouped as (sin articulo); cancelled documents are excluded.

WITH thresholds AS (
    SELECT company, period, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'threshold' AND param_key = 'margin_min_pct'
),
invoice_lines AS (
    SELECT l.* REPLACE (COALESCE(l.item_code, '(sin articulo)') AS item_code),
           CASE WHEN NOT l.is_intercompany THEN (
               SELECT t.value_num FROM thresholds t
               WHERE t.company IN (l.company, '*') AND t.period IN (strftime(l.doc_month, '%Y-%m'), '*')
               ORDER BY t.company = '*', t.period = '*'
               LIMIT 1
           ) END AS min_margin_pct
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet') l
    WHERE l.canceled = 'N'
),
invoices AS (
    SELECT company, doc_month, local_currency, item_code, is_intercompany,
           MAX(min_margin_pct)                                          AS min_margin_pct,
           SUM(COALESCE(quantity, 0))                                   AS qty,
           SUM(amount_local)                                            AS revenue_gross,
           SUM(COALESCE(cost_local, 0))                                 AS cost_gross,
           SUM(CASE WHEN amount_local < COALESCE(cost_local, 0) THEN amount_local ELSE 0 END) AS negative_margin_revenue,
           SUM(CASE WHEN min_margin_pct IS NOT NULL AND amount_local > 0
                     AND 100.0 * (amount_local - COALESCE(cost_local, 0)) / amount_local < min_margin_pct
                    THEN amount_local ELSE 0 END)                       AS below_min_revenue
    FROM invoice_lines
    GROUP BY 1, 2, 3, 4, 5
),
credits AS (
    SELECT company, doc_month, local_currency, COALESCE(item_code, '(sin articulo)') AS item_code, is_intercompany,
           SUM(COALESCE(quantity, 0))                                   AS qty,
           SUM(amount_local)                                            AS revenue,
           SUM(COALESCE(cost_local, 0))                                 AS cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
    GROUP BY 1, 2, 3, 4, 5
),
merged AS (
    SELECT
        COALESCE(i.company, c.company)                                  AS company,
        COALESCE(i.doc_month, c.doc_month)                              AS doc_month,
        COALESCE(i.local_currency, c.local_currency)                    AS local_currency,
        COALESCE(i.item_code, c.item_code)                              AS item_code,
        COALESCE(i.is_intercompany, c.is_intercompany)                  AS is_intercompany,
        i.min_margin_pct,
        COALESCE(i.qty, 0) - COALESCE(c.qty, 0)                         AS qty_net,
        COALESCE(i.revenue_gross, 0) - COALESCE(c.revenue, 0)           AS revenue_net,
        COALESCE(i.cost_gross, 0) - COALESCE(c.cost, 0)                 AS cost_net,
        COALESCE(i.negative_margin_revenue, 0)                          AS negative_margin_revenue,
        COALESCE(i.below_min_revenue, 0)                                AS below_min_revenue
    FROM invoices i
    FULL OUTER JOIN credits c
      ON c.company = i.company AND c.doc_month = i.doc_month AND c.item_code = i.item_code
     AND c.is_intercompany = i.is_intercompany AND c.local_currency = i.local_currency
)
SELECT
    m.company,
    m.doc_month,
    strftime(m.doc_month, '%Y-%m')                                        AS period,
    CASE WHEN m.is_intercompany THEN 'intercompany' ELSE 'external' END   AS scope,
    m.local_currency,
    m.item_code,
    it.item_name,
    it.item_group_code,
    COALESCE(it.item_group_name, 'Sin grupo')                             AS item_group_name,
    COALESCE(x.item_key, 'CODE:' || m.item_code)                          AS item_key,
    ROUND(m.qty_net, 6)                                                   AS qty_net,
    ROUND(m.revenue_net, 2)                                               AS revenue_net_local,
    ROUND(m.cost_net, 2)                                                  AS cost_net_local,
    ROUND(m.revenue_net - m.cost_net, 2)                                  AS gross_profit_net_local,
    CASE WHEN m.revenue_net <> 0 THEN ROUND(100.0 * (m.revenue_net - m.cost_net) / m.revenue_net, 2) END AS margin_pct,
    m.min_margin_pct,
    ROUND(m.negative_margin_revenue, 2)                                   AS negative_margin_revenue_local,
    ROUND(m.below_min_revenue, 2)                                         AS below_min_revenue_local
FROM merged m
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_items/**/*.parquet') it
  ON it.company = m.company AND it.item_code = m.item_code
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet') x
  ON x.company = m.company AND x.item_code = m.item_code
ORDER BY m.company, m.doc_month, scope, m.item_code
