-- sap_b1_sales_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_ar_invoice_lines"]
-- description: Sales per company and month, split between external customers and group companies: invoices, credit memos, net revenue in local and system currency (after the document footer discount), the cost carried by the invoice lines and the resulting gross margin (B1's line gross profit less the footer discount). Cancelled documents are excluded.

WITH invoices AS (
    SELECT company, doc_month, local_currency, sys_currency,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, amount_local_net, amount_sys_net, cost_local,
           gross_profit_local - (amount_local - amount_local_net) AS gross_profit_local,
           gross_profit_sys - (amount_sys - amount_sys_net)       AS gross_profit_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, amount_local_net, amount_sys_net
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
invoice_totals AS (
    SELECT company, doc_month, local_currency, sys_currency, scope,
           COUNT(DISTINCT doc_entry)            AS invoices,
           SUM(amount_local_net)            AS revenue_gross_local,
           SUM(amount_sys_net)              AS revenue_gross_sys,
           SUM(cost_local)                      AS cost_local,
           SUM(gross_profit_local)              AS gross_profit_local,
           SUM(gross_profit_sys)                AS gross_profit_sys
    FROM invoices
    GROUP BY 1, 2, 3, 4, 5
),
credit_totals AS (
    SELECT company, doc_month, scope,
           COUNT(DISTINCT doc_entry)            AS credit_memos,
           SUM(amount_local_net)            AS credit_local,
           SUM(amount_sys_net)              AS credit_sys
    FROM credits
    GROUP BY 1, 2, 3
)
SELECT
    i.company,
    i.doc_month,
    i.scope,
    i.local_currency,
    i.sys_currency,
    i.invoices,
    COALESCE(c.credit_memos, 0)                                     AS credit_memos,
    ROUND(i.revenue_gross_local, 2)                                 AS revenue_gross_local,
    ROUND(COALESCE(c.credit_local, 0), 2)                           AS credit_memos_local,
    ROUND(i.revenue_gross_local - COALESCE(c.credit_local, 0), 2)   AS revenue_net_local,
    ROUND(i.revenue_gross_sys - COALESCE(c.credit_sys, 0), 2)       AS revenue_net_sys,
    ROUND(i.cost_local, 2)                                          AS cost_local,
    ROUND(i.gross_profit_local, 2)                                  AS gross_profit_local,
    ROUND(i.gross_profit_sys, 2)                                    AS gross_profit_sys,
    CASE WHEN i.revenue_gross_local <> 0
         THEN ROUND(100.0 * i.gross_profit_local / i.revenue_gross_local, 2) END AS gross_margin_pct
FROM invoice_totals i
LEFT JOIN credit_totals c
  ON c.company = i.company AND c.doc_month = i.doc_month AND c.scope = i.scope
ORDER BY i.company, i.doc_month, i.scope
