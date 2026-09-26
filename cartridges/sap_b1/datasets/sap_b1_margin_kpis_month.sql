-- sap_b1_margin_kpis_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_business_parameters", "gold/sap_b1/sap_b1_margin_detail_month", "gold/sap_b1/sap_b1_margin_consolidated_month"]
-- description: The five margin indicators per company (and the group where it applies) and month in long form, one row per indicator, dimension and key, in the company's local_currency: margen_bruto and margen_contribucion by total, customer, item (SKU), channel and sales employee over the company's own books (external and group-company sales); destructores = external customers whose gross margin is below the minimum (threshold margin_min_pct, or below zero when it is not set) ranked by the margin lost against that minimum; concentracion_top20 = share of the external gross margin earned from the top 20 % of external customers (at least one), with those customers ranked; margen_vendedor = gross and contribution margin per sales employee. Each row keeps the components (invoiced before and after the footer discount, credit memos, cost, commission) so any difference against Finance can be explained.
-- partition_by: period

WITH detail AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_b1/sap_b1_margin_detail_month/**/*.parquet')
),
thresholds AS (
    SELECT company, period, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'threshold' AND param_key = 'margin_min_pct'
),
dimensioned AS (
    SELECT company, doc_month, period, local_currency, scope, 'total' AS dimension, '' AS dim_key, 'Total' AS dim_label, d.* EXCLUDE (company, doc_month, period, local_currency, scope) FROM detail d
    UNION ALL
    SELECT company, doc_month, period, local_currency, scope, 'cliente', card_code, card_name, d.* EXCLUDE (company, doc_month, period, local_currency, scope) FROM detail d
    UNION ALL
    SELECT company, doc_month, period, local_currency, scope, 'sku', item_code, item_name, d.* EXCLUDE (company, doc_month, period, local_currency, scope) FROM detail d
    UNION ALL
    SELECT company, doc_month, period, local_currency, scope, 'canal', channel_code, channel_name, d.* EXCLUDE (company, doc_month, period, local_currency, scope) FROM detail d
    UNION ALL
    SELECT company, doc_month, period, local_currency, scope, 'vendedor', CAST(slp_code AS VARCHAR), slp_name, d.* EXCLUDE (company, doc_month, period, local_currency, scope) FROM detail d
),
books AS (
    SELECT company, doc_month, period, local_currency, dimension, dim_key,
           MAX(dim_label)                          AS dim_label,
           SUM(invoiced_before_discount_local)     AS invoiced_before_discount,
           SUM(footer_discount_local)              AS footer_discount,
           SUM(credit_memos_local)                 AS credit_memos,
           SUM(revenue_net_local)                  AS revenue_net,
           SUM(cost_net_local)                     AS cost_net,
           SUM(gross_margin_local)                 AS gross_margin,
           SUM(commission_local)                   AS commission,
           SUM(contribution_margin_local)          AS contribution_margin
    FROM dimensioned
    GROUP BY 1, 2, 3, 4, 5, 6
),
external_customers AS (
    SELECT company, doc_month, period, local_currency, card_code AS dim_key,
           MAX(card_name)                          AS dim_label,
           SUM(invoiced_before_discount_local)     AS invoiced_before_discount,
           SUM(footer_discount_local)              AS footer_discount,
           SUM(credit_memos_local)                 AS credit_memos,
           SUM(revenue_net_local)                  AS revenue_net,
           SUM(cost_net_local)                     AS cost_net,
           SUM(gross_margin_local)                 AS gross_margin,
           SUM(commission_local)                   AS commission,
           SUM(contribution_margin_local)          AS contribution_margin
    FROM detail
    WHERE scope = 'external'
    GROUP BY 1, 2, 3, 4, 5
),
with_threshold AS (
    SELECT e.*,
           (SELECT t.value_num FROM thresholds t
             WHERE t.company IN (e.company, '*') AND t.period IN (e.period, '*')
             ORDER BY t.company = '*', t.period = '*'
             LIMIT 1) AS min_margin_pct
    FROM external_customers e
),
destroyers AS (
    SELECT *,
           CASE WHEN min_margin_pct IS NULL THEN -gross_margin
                ELSE revenue_net * min_margin_pct / 100.0 - gross_margin END AS margin_lost
    FROM with_threshold
    WHERE revenue_net > 0
      AND ((min_margin_pct IS NULL AND gross_margin < 0)
        OR (min_margin_pct IS NOT NULL AND 100.0 * gross_margin / revenue_net < min_margin_pct))
),
ranked_customers AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY company, period ORDER BY gross_margin DESC, dim_key) AS margin_rank,
           COUNT(*) OVER (PARTITION BY company, period)                                      AS customers,
           SUM(gross_margin) OVER (PARTITION BY company, period)                             AS total_margin
    FROM with_threshold
),
top20 AS (
    SELECT *, GREATEST(1, CAST(CEIL(customers * 0.2) AS BIGINT)) AS top_n
    FROM ranked_customers
),
concentration AS (
    SELECT company, doc_month, period, local_currency,
           MAX(customers)                                                     AS customers,
           MAX(top_n)                                                         AS top_n,
           SUM(CASE WHEN margin_rank <= top_n THEN gross_margin ELSE 0 END)   AS top_margin,
           MAX(total_margin)                                                  AS total_margin,
           SUM(CASE WHEN margin_rank <= top_n THEN revenue_net ELSE 0 END)    AS top_revenue,
           SUM(revenue_net)                                                   AS total_revenue
    FROM top20
    GROUP BY 1, 2, 3, 4
),
group_total AS (
    SELECT doc_month, strftime(doc_month, '%Y-%m') AS period, local_currency,
           SUM(external_revenue_net_local)          AS revenue_net,
           SUM(consolidated_gross_profit_local)     AS gross_margin
    FROM read_parquet('s3://{bucket}/gold/sap_b1/sap_b1_margin_consolidated_month/**/*.parquet')
    GROUP BY 1, 2, 3
),
group_commission AS (
    SELECT doc_month, local_currency, SUM(commission_local) AS commission
    FROM detail
    WHERE scope = 'external'
    GROUP BY 1, 2
),
rows AS (
    SELECT company, doc_month, period, local_currency, 'margen_bruto' AS indicator, dimension, dim_key, dim_label,
           gross_margin AS value_local,
           CASE WHEN revenue_net <> 0 THEN 100.0 * gross_margin / revenue_net END AS value_pct,
           NULL::BIGINT AS rank_in_period, NULL::DOUBLE AS min_margin_pct,
           invoiced_before_discount, footer_discount, credit_memos, revenue_net, cost_net, gross_margin, commission, contribution_margin
    FROM books
    UNION ALL
    SELECT company, doc_month, period, local_currency, 'margen_contribucion', dimension, dim_key, dim_label,
           contribution_margin,
           CASE WHEN revenue_net <> 0 THEN 100.0 * contribution_margin / revenue_net END,
           NULL, NULL,
           invoiced_before_discount, footer_discount, credit_memos, revenue_net, cost_net, gross_margin, commission, contribution_margin
    FROM books
    UNION ALL
    SELECT company, doc_month, period, local_currency, 'margen_vendedor', dimension, dim_key, dim_label,
           gross_margin,
           CASE WHEN revenue_net <> 0 THEN 100.0 * gross_margin / revenue_net END,
           ROW_NUMBER() OVER (PARTITION BY company, period ORDER BY gross_margin DESC, dim_key),
           NULL,
           invoiced_before_discount, footer_discount, credit_memos, revenue_net, cost_net, gross_margin, commission, contribution_margin
    FROM books
    WHERE dimension = 'vendedor'
    UNION ALL
    SELECT company, doc_month, period, local_currency, 'destructores', 'cliente', dim_key, dim_label,
           margin_lost,
           CASE WHEN revenue_net <> 0 THEN 100.0 * gross_margin / revenue_net END,
           ROW_NUMBER() OVER (PARTITION BY company, period ORDER BY margin_lost DESC, dim_key),
           min_margin_pct,
           invoiced_before_discount, footer_discount, credit_memos, revenue_net, cost_net, gross_margin, commission, contribution_margin
    FROM destroyers
    UNION ALL
    SELECT company, doc_month, period, local_currency, 'concentracion_top20', 'total', '', 'Top 20 % de clientes',
           top_margin,
           CASE WHEN total_margin <> 0 THEN 100.0 * top_margin / total_margin END,
           top_n, NULL,
           NULL, NULL, NULL, top_revenue, NULL, top_margin, NULL, NULL
    FROM concentration
    UNION ALL
    SELECT company, doc_month, period, local_currency, 'concentracion_top20', 'cliente', dim_key, dim_label,
           gross_margin,
           CASE WHEN total_margin <> 0 THEN 100.0 * gross_margin / total_margin END,
           margin_rank, NULL,
           invoiced_before_discount, footer_discount, credit_memos, revenue_net, cost_net, gross_margin, commission, contribution_margin
    FROM top20
    WHERE margin_rank <= top_n
    UNION ALL
    SELECT 'grupo', g.doc_month, g.period, g.local_currency, 'margen_bruto', 'total', '', 'Grupo consolidado',
           g.gross_margin,
           CASE WHEN g.revenue_net <> 0 THEN 100.0 * g.gross_margin / g.revenue_net END,
           NULL, NULL,
           NULL, NULL, NULL, g.revenue_net, g.revenue_net - g.gross_margin, g.gross_margin,
           COALESCE(c.commission, 0), g.gross_margin - COALESCE(c.commission, 0)
    FROM group_total g
    LEFT JOIN group_commission c ON c.doc_month = g.doc_month AND c.local_currency = g.local_currency
    UNION ALL
    SELECT 'grupo', g.doc_month, g.period, g.local_currency, 'margen_contribucion', 'total', '', 'Grupo consolidado',
           g.gross_margin - COALESCE(c.commission, 0),
           CASE WHEN g.revenue_net <> 0 THEN 100.0 * (g.gross_margin - COALESCE(c.commission, 0)) / g.revenue_net END,
           NULL, NULL,
           NULL, NULL, NULL, g.revenue_net, g.revenue_net - g.gross_margin, g.gross_margin,
           COALESCE(c.commission, 0), g.gross_margin - COALESCE(c.commission, 0)
    FROM group_total g
    LEFT JOIN group_commission c ON c.doc_month = g.doc_month AND c.local_currency = g.local_currency
)
SELECT
    company,
    doc_month,
    period,
    local_currency,
    indicator,
    dimension,
    dim_key,
    dim_label,
    ROUND(value_local, 2)                   AS value_local,
    ROUND(value_pct, 4)                     AS value_pct,
    rank_in_period,
    min_margin_pct,
    ROUND(invoiced_before_discount, 2)      AS invoiced_before_discount_local,
    ROUND(footer_discount, 2)               AS footer_discount_local,
    ROUND(credit_memos, 2)                  AS credit_memos_local,
    ROUND(revenue_net, 2)                   AS revenue_net_local,
    ROUND(cost_net, 2)                      AS cost_net_local,
    ROUND(gross_margin, 2)                  AS gross_margin_local,
    ROUND(commission, 2)                    AS commission_local,
    ROUND(contribution_margin, 2)           AS contribution_margin_local
FROM rows
ORDER BY company, doc_month, indicator, dimension, rank_in_period NULLS LAST, dim_key
