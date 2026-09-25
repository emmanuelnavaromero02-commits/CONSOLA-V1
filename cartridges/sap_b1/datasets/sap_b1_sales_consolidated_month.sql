-- sap_b1_sales_consolidated_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_ar_invoice_lines"]
-- description: Group sales per month with intercompany sales eliminated: only invoices to external customers count, per local currency, with the eliminated intercompany amount shown next to it. A month where a company's currency differs from the others is reported on its own row, never mixed.

WITH lines AS (
    SELECT company, doc_month, local_currency, is_intercompany, doc_entry,
           amount_local, cost_local, gross_profit_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month, local_currency, is_intercompany, amount_local
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
by_month AS (
    SELECT doc_month, local_currency,
           COUNT(DISTINCT company)                                           AS companies,
           COUNT(DISTINCT CASE WHEN NOT is_intercompany THEN company || ':' || doc_entry END) AS external_invoices,
           SUM(CASE WHEN NOT is_intercompany THEN amount_local ELSE 0 END)   AS external_revenue_gross_local,
           SUM(CASE WHEN is_intercompany THEN amount_local ELSE 0 END)       AS intercompany_eliminated_local,
           SUM(CASE WHEN NOT is_intercompany THEN cost_local ELSE 0 END)     AS external_cost_local,
           SUM(CASE WHEN NOT is_intercompany THEN gross_profit_local ELSE 0 END) AS external_gross_profit_local
    FROM lines
    GROUP BY 1, 2
),
credit_month AS (
    SELECT doc_month, local_currency,
           SUM(CASE WHEN NOT is_intercompany THEN amount_local ELSE 0 END)   AS external_credit_local,
           SUM(CASE WHEN is_intercompany THEN amount_local ELSE 0 END)       AS intercompany_credit_eliminated_local
    FROM credits
    GROUP BY 1, 2
)
SELECT
    m.doc_month,
    m.local_currency,
    m.companies,
    m.external_invoices,
    ROUND(m.external_revenue_gross_local, 2)                                    AS revenue_gross_local,
    ROUND(COALESCE(c.external_credit_local, 0), 2)                              AS credit_memos_local,
    ROUND(m.external_revenue_gross_local - COALESCE(c.external_credit_local, 0), 2) AS revenue_net_local,
    ROUND(m.intercompany_eliminated_local - COALESCE(c.intercompany_credit_eliminated_local, 0), 2) AS intercompany_eliminated_local,
    ROUND(m.external_cost_local, 2)                                             AS cost_local,
    ROUND(m.external_gross_profit_local, 2)                                     AS gross_profit_local,
    CASE WHEN m.external_revenue_gross_local <> 0
         THEN ROUND(100.0 * m.external_gross_profit_local / m.external_revenue_gross_local, 2) END AS gross_margin_pct
FROM by_month m
LEFT JOIN credit_month c ON c.doc_month = m.doc_month AND c.local_currency = m.local_currency
ORDER BY m.doc_month, m.local_currency
