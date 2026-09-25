-- sap_b1_pnl_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_journal_lines"]
-- description: The accounting view per company and month from the journal: income accounts (type I) as revenue, expense accounts (type E) as expenses, and the operating result, in local and system currency. Reversals land in the month they were posted, so a month can differ from the document view; period totals agree.

WITH lines AS (
    SELECT company, doc_month, local_currency, sys_currency, account_type,
           net_debit_local, net_debit_sys
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_journal_lines/**/*.parquet')
    WHERE account_type IN ('I', 'E')
)
SELECT
    company,
    doc_month,
    local_currency,
    sys_currency,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_local ELSE 0 END), 2)  AS revenue_local,
    ROUND(SUM(CASE WHEN account_type = 'E' THEN net_debit_local ELSE 0 END), 2)   AS expenses_local,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_local ELSE net_debit_local * -1 END), 2) AS operating_result_local,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_sys ELSE 0 END), 2)    AS revenue_sys,
    ROUND(SUM(CASE WHEN account_type = 'E' THEN net_debit_sys ELSE 0 END), 2)     AS expenses_sys,
    ROUND(SUM(CASE WHEN account_type = 'I' THEN -net_debit_sys ELSE net_debit_sys * -1 END), 2) AS operating_result_sys
FROM lines
GROUP BY 1, 2, 3, 4
ORDER BY company, doc_month
