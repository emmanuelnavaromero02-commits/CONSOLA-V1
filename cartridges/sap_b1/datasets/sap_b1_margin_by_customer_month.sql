-- sap_b1_margin_by_customer_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_customer_crosswalk", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Net margin per company, month and customer: invoices minus credit memos for revenue and cost, the customer identity shared across companies, and, for external customers, the minimum margin threshold (threshold margin_min_pct) with the customers below it; cancelled documents are excluded.

WITH invoices AS (
    SELECT company, doc_month, local_currency, card_code, is_intercompany,
           MAX(card_name)                       AS card_name,
           COUNT(DISTINCT doc_entry)            AS invoices,
           SUM(amount_local)                    AS revenue_gross,
           SUM(COALESCE(cost_local, 0))         AS cost_gross
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
    GROUP BY 1, 2, 3, 4, 5
),
credits AS (
    SELECT company, doc_month, local_currency, card_code, is_intercompany,
           MAX(card_name)                       AS card_name,
           COUNT(DISTINCT doc_entry)            AS credit_memos,
           SUM(amount_local)                    AS credit_revenue,
           SUM(COALESCE(cost_local, 0))         AS credit_cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
    GROUP BY 1, 2, 3, 4, 5
),
merged AS (
    SELECT
        COALESCE(i.company, c.company)                      AS company,
        COALESCE(i.doc_month, c.doc_month)                  AS doc_month,
        COALESCE(i.local_currency, c.local_currency)        AS local_currency,
        COALESCE(i.card_code, c.card_code)                  AS card_code,
        COALESCE(i.card_name, c.card_name)                  AS card_name,
        COALESCE(i.is_intercompany, c.is_intercompany)      AS is_intercompany,
        COALESCE(i.invoices, 0)                             AS invoices,
        COALESCE(c.credit_memos, 0)                         AS credit_memos,
        COALESCE(i.revenue_gross, 0)                        AS revenue_gross,
        COALESCE(c.credit_revenue, 0)                       AS credit_revenue,
        COALESCE(i.cost_gross, 0) - COALESCE(c.credit_cost, 0) AS cost_net
    FROM invoices i
    FULL OUTER JOIN credits c
      ON c.company = i.company AND c.doc_month = i.doc_month AND c.card_code = i.card_code
     AND c.is_intercompany = i.is_intercompany AND c.local_currency = i.local_currency
),
thresholds AS (
    SELECT company, period, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'threshold' AND param_key = 'margin_min_pct'
),
measured AS (
    SELECT m.*,
           m.revenue_gross - m.credit_revenue                           AS revenue_net,
           m.revenue_gross - m.credit_revenue - m.cost_net              AS gross_profit_net,
           CASE WHEN NOT m.is_intercompany THEN (
               SELECT t.value_num FROM thresholds t
               WHERE t.company IN (m.company, '*') AND t.period IN (strftime(m.doc_month, '%Y-%m'), '*')
               ORDER BY t.company = '*', t.period = '*'
               LIMIT 1
           ) END                                                        AS min_margin_pct
    FROM merged m
)
SELECT
    m.company,
    m.doc_month,
    strftime(m.doc_month, '%Y-%m')                                        AS period,
    CASE WHEN m.is_intercompany THEN 'intercompany' ELSE 'external' END   AS scope,
    m.local_currency,
    m.card_code,
    m.card_name,
    COALESCE(x.customer_key, m.company || ':' || m.card_code)             AS customer_key,
    COALESCE(x.match_method, CASE WHEN m.is_intercompany THEN 'group_company' ELSE 'not_in_master' END) AS match_method,
    m.invoices,
    m.credit_memos,
    ROUND(m.revenue_gross, 2)                                             AS revenue_gross_local,
    ROUND(m.credit_revenue, 2)                                            AS credit_memos_local,
    ROUND(m.revenue_net, 2)                                               AS revenue_net_local,
    ROUND(m.cost_net, 2)                                                  AS cost_net_local,
    ROUND(m.gross_profit_net, 2)                                          AS gross_profit_net_local,
    CASE WHEN m.revenue_net <> 0 THEN ROUND(100.0 * m.gross_profit_net / m.revenue_net, 2) END AS margin_pct,
    m.min_margin_pct,
    m.gross_profit_net < 0                                                AS negative_margin,
    CASE WHEN m.min_margin_pct IS NULL OR m.revenue_net = 0 THEN NULL
         ELSE 100.0 * m.gross_profit_net / m.revenue_net < m.min_margin_pct END AS below_min
FROM measured m
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_customer_crosswalk/**/*.parquet') x
  ON x.company = m.company AND x.card_code = m.card_code
ORDER BY m.company, m.doc_month, scope, m.card_code
