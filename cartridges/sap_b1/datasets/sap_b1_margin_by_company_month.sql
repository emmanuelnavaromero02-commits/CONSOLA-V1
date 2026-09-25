-- sap_b1_margin_by_company_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Net margin per company, month and scope (external customers or group companies) with the minimum margin threshold (threshold margin_min_pct, external sales only), how many customers fall below it or sell at a loss and the revenue they carry; cancelled documents are excluded.

WITH sales AS (
    SELECT company, doc_month, local_currency, card_code, is_intercompany,
           amount_local AS revenue, COALESCE(cost_local, 0) AS cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
    UNION ALL
    SELECT company, doc_month, local_currency, card_code, is_intercompany,
           -amount_local, -COALESCE(cost_local, 0)
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
thresholds AS (
    SELECT company, period, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'threshold' AND param_key = 'margin_min_pct'
),
customers AS (
    SELECT company, doc_month, local_currency, card_code, is_intercompany,
           SUM(revenue) AS revenue, SUM(revenue) - SUM(cost) AS gross_profit
    FROM sales
    GROUP BY 1, 2, 3, 4, 5
),
rated AS (
    SELECT c.*,
           CASE WHEN NOT c.is_intercompany THEN (
               SELECT t.value_num FROM thresholds t
               WHERE t.company IN (c.company, '*') AND t.period IN (strftime(c.doc_month, '%Y-%m'), '*')
               ORDER BY t.company = '*', t.period = '*'
               LIMIT 1
           ) END AS min_margin_pct
    FROM customers c
)
SELECT
    company,
    doc_month,
    strftime(doc_month, '%Y-%m')                                           AS period,
    CASE WHEN is_intercompany THEN 'intercompany' ELSE 'external' END     AS scope,
    local_currency,
    COUNT(*)                                                               AS customers,
    ROUND(SUM(revenue), 2)                                                 AS revenue_net_local,
    ROUND(SUM(revenue) - SUM(gross_profit), 2)                            AS cost_net_local,
    ROUND(SUM(gross_profit), 2)                                            AS gross_profit_net_local,
    CASE WHEN SUM(revenue) <> 0 THEN ROUND(100.0 * SUM(gross_profit) / SUM(revenue), 2) END AS margin_pct,
    MAX(min_margin_pct)                                                    AS min_margin_pct,
    COUNT(*) FILTER (WHERE min_margin_pct IS NOT NULL AND revenue <> 0 AND 100.0 * gross_profit / revenue < min_margin_pct) AS customers_below_min,
    COUNT(*) FILTER (WHERE gross_profit < 0)                               AS customers_negative,
    ROUND(COALESCE(SUM(revenue) FILTER (WHERE min_margin_pct IS NOT NULL AND revenue <> 0 AND 100.0 * gross_profit / revenue < min_margin_pct), 0), 2) AS revenue_below_min_local
FROM rated
GROUP BY company, doc_month, is_intercompany, local_currency
ORDER BY company, doc_month, scope
