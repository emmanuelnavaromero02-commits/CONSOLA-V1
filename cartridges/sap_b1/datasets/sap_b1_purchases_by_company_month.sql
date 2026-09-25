-- sap_b1_purchases_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ap_credit_memo_lines", "silver/sap_b1/sap_b1_ap_invoice_lines"]
-- description: Purchases per company and month from supplier invoices net of supplier credit memos, split between external suppliers and group companies, in local and system currency. Cancelled documents are excluded.

WITH invoices AS (
    SELECT company, doc_month, local_currency, sys_currency,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, card_code, amount_local, amount_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
),
credits AS (
    SELECT company, doc_month,
           CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END AS scope,
           doc_entry, amount_local, amount_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
invoice_totals AS (
    SELECT company, doc_month, local_currency, sys_currency, scope,
           COUNT(DISTINCT doc_entry) AS supplier_invoices,
           COUNT(DISTINCT card_code) AS suppliers,
           SUM(amount_local)         AS purchases_gross_local,
           SUM(amount_sys)           AS purchases_gross_sys
    FROM invoices
    GROUP BY 1, 2, 3, 4, 5
),
credit_totals AS (
    SELECT company, doc_month, scope,
           COUNT(DISTINCT doc_entry) AS credit_memos,
           SUM(amount_local)         AS credit_local,
           SUM(amount_sys)           AS credit_sys
    FROM credits
    GROUP BY 1, 2, 3
)
SELECT
    i.company,
    i.doc_month,
    i.scope,
    i.local_currency,
    i.sys_currency,
    i.supplier_invoices,
    i.suppliers,
    COALESCE(c.credit_memos, 0)                                     AS credit_memos,
    ROUND(i.purchases_gross_local, 2)                               AS purchases_gross_local,
    ROUND(COALESCE(c.credit_local, 0), 2)                           AS credit_memos_local,
    ROUND(i.purchases_gross_local - COALESCE(c.credit_local, 0), 2) AS purchases_net_local,
    ROUND(i.purchases_gross_sys - COALESCE(c.credit_sys, 0), 2)     AS purchases_net_sys
FROM invoice_totals i
LEFT JOIN credit_totals c
  ON c.company = i.company AND c.doc_month = i.doc_month AND c.scope = i.scope
ORDER BY i.company, i.doc_month, i.scope
