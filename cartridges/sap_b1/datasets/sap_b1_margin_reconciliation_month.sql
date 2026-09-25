-- sap_b1_margin_reconciliation_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_delivery_lines", "silver/sap_b1/sap_b1_journal_lines", "silver/sap_b1/sap_b1_oact_latest", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Documents against the ledger per company and month: the documents behind the dashboards (net of the footer discount and of credit memos), the timing that separates them from the ledger (cancellations reversed in a later month, cost posted at delivery), the ledger's revenue and cost of sales by origin (documents, production, manual entries, other), and what is left unexplained; status cuadra when the unexplained revenue and document cost are within the tolerance (setting reconciliation_tolerance_pct, 1 by default) of the ledger, diferencia otherwise, sin_contabilidad when the month has no ledger lines. Finance's manual run is compared indicator by indicator in sap_b1_kpi_reconciliation.

WITH params AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
),
invoice_lines AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
),
credit_lines AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
),
documents AS (
    SELECT company, doc_month, local_currency,
           SUM(CASE WHEN canceled = 'N' THEN amount_local_net ELSE 0 END)                         AS revenue,
           SUM(CASE WHEN canceled = 'N' THEN COALESCE(cost_local, 0) ELSE 0 END)              AS cogs,
           SUM(CASE WHEN canceled = 'Y' THEN amount_local_net WHEN canceled = 'C' THEN -amount_local_net ELSE 0 END) AS revenue_timing,
           SUM(CASE WHEN canceled = 'N' AND base_type = 15 THEN COALESCE(cost_local, 0) ELSE 0 END) AS invoiced_delivery_cost
    FROM invoice_lines
    GROUP BY 1, 2, 3
    UNION ALL
    SELECT company, doc_month, local_currency,
           -SUM(amount_local_net), -SUM(COALESCE(cost_local, 0)), 0, 0
    FROM credit_lines
    WHERE canceled = 'N'
    GROUP BY 1, 2, 3
),
deliveries AS (
    SELECT company, doc_month, local_currency, SUM(COALESCE(cost_local, 0)) AS delivered_cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_delivery_lines/**/*.parquet')
    WHERE canceled = 'N'
    GROUP BY 1, 2, 3
),
accounts AS (
    SELECT company, acct_code, group_mask
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oact_latest/**/*.parquet')
),
account_lists AS (
    SELECT company, param_key, TRIM(code) AS code
    FROM params, UNNEST(string_split(value_text, ',')) AS t(code)
    WHERE kind = 'account'
),
journal AS (
    SELECT j.company, j.doc_month, j.local_currency, j.account, j.origin_obj_type,
           j.debit_local, j.credit_local, a.group_mask
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_journal_lines/**/*.parquet') j
    LEFT JOIN accounts a ON a.company = j.company AND a.acct_code = j.account
),
classified AS (
    SELECT j.*,
           CASE WHEN EXISTS (SELECT 1 FROM account_lists l WHERE l.param_key = 'revenue' AND l.company IN (j.company, '*'))
                THEN EXISTS (
                    SELECT 1 FROM account_lists l
                    WHERE l.param_key = 'revenue' AND l.company IN (j.company, '*')
                      AND (j.account = l.code OR (l.code LIKE '%*' AND starts_with(j.account, rtrim(l.code, '*'))))
                )
                ELSE j.group_mask = 4 END                                   AS is_revenue,
           CASE WHEN EXISTS (SELECT 1 FROM account_lists l WHERE l.param_key = 'cogs' AND l.company IN (j.company, '*'))
                THEN EXISTS (
                    SELECT 1 FROM account_lists l
                    WHERE l.param_key = 'cogs' AND l.company IN (j.company, '*')
                      AND (j.account = l.code OR (l.code LIKE '%*' AND starts_with(j.account, rtrim(l.code, '*'))))
                )
                ELSE j.group_mask = 5 END                                   AS is_cogs
    FROM journal j
),
ledger AS (
    SELECT company, doc_month, local_currency,
           SUM(CASE WHEN is_revenue THEN credit_local - debit_local ELSE 0 END)                                  AS gl_revenue,
           SUM(CASE WHEN is_cogs THEN debit_local - credit_local ELSE 0 END)                                     AS gl_cogs,
           SUM(CASE WHEN is_cogs AND origin_obj_type IN ('13', '14', '15', '16') THEN debit_local - credit_local ELSE 0 END) AS gl_cogs_documents,
           SUM(CASE WHEN is_cogs AND origin_obj_type IN ('59', '60', '202') THEN debit_local - credit_local ELSE 0 END)     AS gl_cogs_production,
           SUM(CASE WHEN is_cogs AND origin_obj_type = '30' THEN debit_local - credit_local ELSE 0 END)                     AS gl_cogs_manual
    FROM classified
    GROUP BY 1, 2, 3
),
keys AS (
    SELECT DISTINCT company, doc_month, local_currency FROM documents
    UNION
    SELECT DISTINCT company, doc_month, local_currency FROM ledger
),
tolerances AS (
    SELECT company, value_num
    FROM params
    WHERE kind = 'setting' AND param_key = 'reconciliation_tolerance_pct' AND value_num IS NOT NULL
),
combined AS (
    SELECT
        k.company, k.doc_month, k.local_currency,
        COALESCE(SUM(d.revenue), 0)                                   AS doc_revenue,
        COALESCE(SUM(d.cogs), 0)                                      AS doc_cogs,
        COALESCE(SUM(d.revenue_timing), 0)                            AS revenue_timing,
        COALESCE(ANY_VALUE(dl.delivered_cost), 0) - COALESCE(SUM(d.invoiced_delivery_cost), 0) AS cogs_timing,
        COALESCE(ANY_VALUE(l.gl_revenue), 0)                          AS gl_revenue,
        COALESCE(ANY_VALUE(l.gl_cogs), 0)                             AS gl_cogs,
        COALESCE(ANY_VALUE(l.gl_cogs_documents), 0)                   AS gl_cogs_documents,
        COALESCE(ANY_VALUE(l.gl_cogs_production), 0)                  AS gl_cogs_production,
        COALESCE(ANY_VALUE(l.gl_cogs_manual), 0)                      AS gl_cogs_manual,
        COUNT(l.company) > 0                                          AS has_ledger,
        COALESCE(
            ANY_VALUE((SELECT t.value_num FROM tolerances t WHERE t.company IN (k.company, '*') ORDER BY t.company = '*' LIMIT 1)),
            1.0
        )                                                             AS tolerance_pct
    FROM keys k
    LEFT JOIN documents d ON d.company = k.company AND d.doc_month = k.doc_month AND d.local_currency = k.local_currency
    LEFT JOIN deliveries dl ON dl.company = k.company AND dl.doc_month = k.doc_month AND dl.local_currency = k.local_currency
    LEFT JOIN ledger l ON l.company = k.company AND l.doc_month = k.doc_month AND l.local_currency = k.local_currency
    GROUP BY 1, 2, 3
),
platform AS (
    SELECT *,
           doc_revenue + revenue_timing                                                         AS platform_revenue,
           doc_cogs + cogs_timing + gl_cogs_production + gl_cogs_manual
             + (gl_cogs - gl_cogs_documents - gl_cogs_production - gl_cogs_manual)             AS platform_cogs
    FROM combined
),
diffs AS (
    SELECT *,
           gl_revenue - platform_revenue                                                        AS revenue_residual,
           gl_cogs_documents - doc_cogs - cogs_timing                                           AS cogs_residual,
           CASE WHEN gl_revenue <> 0 THEN 100.0 * (gl_revenue - platform_revenue) / abs(gl_revenue) END AS revenue_residual_pct,
           CASE WHEN gl_cogs_documents <> 0
                THEN 100.0 * (gl_cogs_documents - doc_cogs - cogs_timing) / abs(gl_cogs_documents) END AS cogs_residual_pct
    FROM platform
)
SELECT
    company,
    doc_month,
    local_currency,
    ROUND(doc_revenue, 2)                                           AS doc_revenue_net_local,
    ROUND(doc_cogs, 2)                                              AS doc_cogs_net_local,
    ROUND(doc_revenue - doc_cogs, 2)                                AS doc_gross_profit_local,
    ROUND(revenue_timing, 2)                                        AS revenue_timing_local,
    ROUND(cogs_timing, 2)                                           AS cogs_timing_local,
    ROUND(gl_revenue, 2)                                            AS gl_revenue_local,
    ROUND(gl_cogs, 2)                                               AS gl_cogs_local,
    ROUND(gl_cogs_documents, 2)                                     AS gl_cogs_documents_local,
    ROUND(gl_cogs_production, 2)                                    AS gl_cogs_production_local,
    ROUND(gl_cogs_manual, 2)                                        AS gl_cogs_manual_local,
    ROUND(gl_cogs - gl_cogs_documents - gl_cogs_production - gl_cogs_manual, 2) AS gl_cogs_other_local,
    ROUND(revenue_residual, 2)                                      AS revenue_residual_local,
    ROUND(cogs_residual, 2)                                         AS cogs_residual_local,
    ROUND(revenue_residual_pct, 4)                                  AS revenue_residual_pct,
    ROUND(cogs_residual_pct, 4)                                     AS cogs_residual_pct,
    ROUND(platform_revenue, 2)                                      AS platform_revenue_local,
    ROUND(platform_cogs, 2)                                         AS platform_cogs_local,
    ROUND(platform_revenue - platform_cogs, 2)                      AS platform_gross_profit_local,
    tolerance_pct,
    CASE
        WHEN NOT has_ledger THEN 'sin_contabilidad'
        WHEN abs(revenue_residual) <= abs(gl_revenue) * tolerance_pct / 100.0
         AND abs(cogs_residual) <= abs(gl_cogs_documents) * tolerance_pct / 100.0 THEN 'cuadra'
        ELSE 'diferencia'
    END                                                             AS status
FROM diffs
ORDER BY company, doc_month, local_currency
