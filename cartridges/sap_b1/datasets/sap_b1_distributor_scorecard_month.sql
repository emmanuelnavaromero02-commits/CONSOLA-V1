-- sap_b1_distributor_scorecard_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_inventory_movements", "silver/sap_b1/sap_b1_stock_on_hand", "silver/sap_b1/sap_b1_obtq_latest", "silver/sap_b1/sap_b1_obtn_latest", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Monthly traffic light per buying group company (distributor), amounts in its local_currency except the sell-in amount, which is in the selling company's currency (sell_in_currency, empty when sellers differ): sell-in from the group, sell-out and its growth against the previous month and the same month a year earlier, the sell-out / sell-in ratio over three months (units sold to external customers ÷ units bought from the group), days of stock in the channel, distributor margin and, for the latest month, the share of batch stock expiring within the horizon (setting expiry_horizon_days, 90 by default); each metric is coloured against its threshold and the overall colour is the worst one.

WITH lines AS (
    SELECT company, doc_month, local_currency, is_intercompany, counterparty_company, item_code,
           COALESCE(quantity, 0) AS qty, amount_local_net AS amount, COALESCE(cost_local, 0) AS cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
    UNION ALL
    SELECT company, doc_month, local_currency, is_intercompany, counterparty_company, item_code,
           -COALESCE(quantity, 0), -amount_local_net, -COALESCE(cost_local, 0)
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
distributors AS (
    SELECT DISTINCT counterparty_company AS company
    FROM lines WHERE is_intercompany AND counterparty_company IS NOT NULL
),
bought_items AS (
    SELECT DISTINCT counterparty_company AS company, item_code
    FROM lines WHERE is_intercompany AND counterparty_company IS NOT NULL AND item_code IS NOT NULL
),
monthly AS (
    SELECT company AS distributor, doc_month, MAX(local_currency) AS local_currency,
           SUM(CASE WHEN NOT is_intercompany THEN amount ELSE 0 END) AS sell_out_revenue,
           SUM(CASE WHEN NOT is_intercompany THEN amount - cost ELSE 0 END) AS sell_out_gross_profit,
           SUM(CASE WHEN NOT is_intercompany AND item_code IS NOT NULL THEN qty ELSE 0 END) AS sell_out_qty
    FROM lines
    WHERE company IN (SELECT company FROM distributors)
    GROUP BY 1, 2
),
sell_in AS (
    SELECT counterparty_company AS distributor, doc_month,
           SUM(CASE WHEN item_code IS NOT NULL THEN qty ELSE 0 END) AS sell_in_qty,
           CASE WHEN COUNT(DISTINCT local_currency) = 1 THEN SUM(amount) END AS sell_in_amount,
           CASE WHEN COUNT(DISTINCT local_currency) = 1 THEN MAX(local_currency) END AS sell_in_currency
    FROM lines
    WHERE is_intercompany AND counterparty_company IN (SELECT company FROM distributors)
    GROUP BY 1, 2
),
stock_now AS (
    SELECT s.company, SUM(s.on_hand) AS on_hand
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet') s
    JOIN bought_items b ON b.company = s.company AND b.item_code = s.item_code
    GROUP BY 1
),
moves AS (
    SELECT m.company, CAST(DATE_TRUNC('month', m.doc_date) AS DATE) AS m, SUM(m.net_qty) AS net
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_inventory_movements/**/*.parquet') m
    JOIN bought_items b ON b.company = m.company AND b.item_code = m.item_code
    GROUP BY 1, 2
),
stock_end AS (
    SELECT mo.distributor, mo.doc_month,
           COALESCE(ANY_VALUE(s.on_hand), 0) - COALESCE(SUM(CASE WHEN mv.m > mo.doc_month THEN mv.net ELSE 0 END), 0) AS stock_end_qty
    FROM monthly mo
    LEFT JOIN stock_now s ON s.company = mo.distributor
    LEFT JOIN moves mv ON mv.company = mo.distributor
    GROUP BY 1, 2
),
params AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
),
horizon AS (
    SELECT COALESCE(MAX(value_num) FILTER (WHERE company = '*'), 90) AS days
    FROM params WHERE kind = 'setting' AND param_key = 'expiry_horizon_days'
),
batches AS (
    SELECT q.company, q.quantity, n.exp_date, q.load_date
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtq_latest/**/*.parquet') q
    JOIN bought_items b ON b.company = q.company AND b.item_code = q.item_code
    LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtn_latest/**/*.parquet') n
      ON n.company = q.company AND n.item_code = q.item_code AND n.sys_number = q.sys_number
    WHERE q.quantity > 0
),
expiry AS (
    SELECT b.company,
           100.0 * SUM(CASE WHEN b.exp_date IS NOT NULL
                             AND CAST(b.exp_date AS DATE) <= CAST(b.load_date AS DATE) + CAST((SELECT days FROM horizon) AS INTEGER)
                            THEN b.quantity ELSE 0 END) / NULLIF(SUM(b.quantity), 0) AS expiry_exposed_pct
    FROM batches b
    GROUP BY 1
),
measured AS (
    SELECT mo.*,
           COALESCE(si.sell_in_qty, 0) AS sell_in_qty,
           si.sell_in_amount,
           si.sell_in_currency,
           se.stock_end_qty,
           LAG(mo.sell_out_revenue) OVER (PARTITION BY mo.distributor ORDER BY mo.doc_month) AS prev_revenue,
           LAG(mo.doc_month) OVER (PARTITION BY mo.distributor ORDER BY mo.doc_month) AS prev_month,
           SUM(mo.sell_out_qty) OVER (PARTITION BY mo.distributor ORDER BY mo.doc_month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS sold_3m,
           SUM(COALESCE(si.sell_in_qty, 0)) OVER (PARTITION BY mo.distributor ORDER BY mo.doc_month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS bought_3m,
           MAX(mo.doc_month) OVER (PARTITION BY mo.distributor) AS latest_month
    FROM monthly mo
    LEFT JOIN stock_end se ON se.distributor = mo.distributor AND se.doc_month = mo.doc_month
    LEFT JOIN sell_in si ON si.distributor = mo.distributor AND si.doc_month = mo.doc_month
),
metrics AS (
    SELECT m.*,
           y.sell_out_revenue AS year_ago_revenue,
           CASE WHEN m.prev_month = CAST(m.doc_month - INTERVAL 1 MONTH AS DATE) AND m.prev_revenue <> 0
                THEN 100.0 * (m.sell_out_revenue - m.prev_revenue) / abs(m.prev_revenue) END AS growth_mom_pct,
           CASE WHEN y.sell_out_revenue <> 0
                THEN 100.0 * (m.sell_out_revenue - y.sell_out_revenue) / abs(y.sell_out_revenue) END AS growth_yoy_pct,
           CASE WHEN m.bought_3m > 0 THEN 100.0 * m.sold_3m / m.bought_3m END AS sellout_sellin_3m_pct,
           CASE WHEN m.sold_3m > 0 THEN m.stock_end_qty / (m.sold_3m / 90.0) END AS channel_days,
           CASE WHEN m.sell_out_revenue <> 0 THEN 100.0 * m.sell_out_gross_profit / m.sell_out_revenue END AS margin_pct,
           CASE WHEN m.doc_month = m.latest_month THEN e.expiry_exposed_pct END AS expiry_exposed_pct
    FROM measured m
    LEFT JOIN monthly y ON y.distributor = m.distributor AND y.doc_month = CAST(m.doc_month - INTERVAL 12 MONTH AS DATE)
    LEFT JOIN expiry e ON e.company = m.distributor
),
limits AS (
    SELECT m.distributor, m.doc_month, k.param_key,
           (SELECT p.value_num FROM params p
             WHERE p.kind = 'threshold' AND p.param_key = k.param_key
               AND p.company IN (m.distributor, '*') AND p.period IN (strftime(m.doc_month, '%Y-%m'), '*')
             ORDER BY p.company = '*', p.period = '*' LIMIT 1) AS value
    FROM metrics m
    CROSS JOIN (VALUES ('sellout_growth_min_pct'), ('sellout_sellin_min_pct'), ('channel_days_max'),
                       ('distributor_margin_min_pct'), ('expiry_exposed_max_pct')) AS k(param_key)
),
pivoted AS (
    SELECT m.*,
           MAX(l.value) FILTER (WHERE l.param_key = 'sellout_growth_min_pct') AS growth_min,
           MAX(l.value) FILTER (WHERE l.param_key = 'sellout_sellin_min_pct') AS sellout_sellin_min,
           MAX(l.value) FILTER (WHERE l.param_key = 'channel_days_max') AS channel_days_max,
           MAX(l.value) FILTER (WHERE l.param_key = 'distributor_margin_min_pct') AS margin_min,
           MAX(l.value) FILTER (WHERE l.param_key = 'expiry_exposed_max_pct') AS expiry_max
    FROM metrics m
    LEFT JOIN limits l ON l.distributor = m.distributor AND l.doc_month = m.doc_month
    GROUP BY ALL
),
coloured AS (
    SELECT p.*,
           CASE WHEN p.growth_min IS NULL OR p.growth_yoy_pct IS NULL THEN 'sin_umbral'
                WHEN p.growth_yoy_pct < p.growth_min THEN 'rojo'
                WHEN p.growth_yoy_pct < p.growth_min + 5 THEN 'amarillo' ELSE 'verde' END AS growth_color,
           CASE WHEN p.sellout_sellin_min IS NULL OR p.sellout_sellin_3m_pct IS NULL THEN 'sin_umbral'
                WHEN p.sellout_sellin_3m_pct < p.sellout_sellin_min THEN 'rojo'
                WHEN p.sellout_sellin_3m_pct < p.sellout_sellin_min + 5 THEN 'amarillo' ELSE 'verde' END AS sellout_sellin_color,
           CASE WHEN p.channel_days_max IS NULL OR p.channel_days IS NULL THEN 'sin_umbral'
                WHEN p.channel_days > p.channel_days_max THEN 'rojo'
                WHEN p.channel_days > 0.9 * p.channel_days_max THEN 'amarillo' ELSE 'verde' END AS channel_days_color,
           CASE WHEN p.margin_min IS NULL OR p.margin_pct IS NULL THEN 'sin_umbral'
                WHEN p.margin_pct < p.margin_min THEN 'rojo'
                WHEN p.margin_pct < p.margin_min + 2 THEN 'amarillo' ELSE 'verde' END AS margin_color,
           CASE WHEN p.expiry_max IS NULL OR p.expiry_exposed_pct IS NULL THEN 'sin_umbral'
                WHEN p.expiry_exposed_pct > p.expiry_max THEN 'rojo'
                WHEN p.expiry_exposed_pct > 0.8 * p.expiry_max THEN 'amarillo' ELSE 'verde' END AS expiry_color
    FROM pivoted p
)
SELECT
    distributor,
    doc_month,
    strftime(doc_month, '%Y-%m')                            AS period,
    local_currency,
    ROUND(sell_out_revenue, 2)                              AS sell_out_revenue_local,
    ROUND(sell_out_qty, 6)                                  AS sell_out_qty,
    ROUND(sell_in_qty, 6)                                   AS sell_in_qty,
    ROUND(sell_in_amount, 2)                                AS sell_in_amount_local,
    sell_in_currency,
    ROUND(stock_end_qty, 6)                                 AS stock_end_qty,
    ROUND(growth_mom_pct, 2)                                AS growth_mom_pct,
    ROUND(growth_yoy_pct, 2)                                AS growth_yoy_pct,
    ROUND(sellout_sellin_3m_pct, 2)                         AS sellout_sellin_3m_pct,
    ROUND(channel_days, 1)                                  AS channel_days,
    ROUND(margin_pct, 2)                                    AS margin_pct,
    ROUND(expiry_exposed_pct, 2)                            AS expiry_exposed_pct,
    growth_color,
    sellout_sellin_color,
    channel_days_color,
    margin_color,
    expiry_color,
    CASE WHEN 'rojo' IN (growth_color, sellout_sellin_color, channel_days_color, margin_color, expiry_color) THEN 'rojo'
         WHEN 'amarillo' IN (growth_color, sellout_sellin_color, channel_days_color, margin_color, expiry_color) THEN 'amarillo'
         WHEN 'verde' IN (growth_color, sellout_sellin_color, channel_days_color, margin_color, expiry_color) THEN 'verde'
         ELSE 'sin_umbral' END                              AS overall_color
FROM coloured
ORDER BY distributor, doc_month
