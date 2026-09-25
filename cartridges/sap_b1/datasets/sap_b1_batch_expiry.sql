-- sap_b1_batch_expiry  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_obtq_latest", "silver/sap_b1/sap_b1_obtn_latest", "silver/sap_b1/sap_b1_stock_on_hand", "silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_item_crosswalk", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Batch stock per company, warehouse, item and batch with its expiry date as of the stock snapshot: days to expiry, bucket, value at average cost in the company's local_currency, the 90-day average daily sales of the item in that company, the units expected to expire unsold when the sellable batches are sold first-expired-first-out at that pace (an expired batch is entirely at risk), and the group company that sells the same item fastest as a transfer candidate; within_horizon uses setting expiry_horizon_days (90 by default).

WITH batches AS (
    SELECT q.company, q.whs_code AS warehouse, q.item_code, q.sys_number, q.quantity AS qty,
           CAST(n.exp_date AS DATE) AS exp_date, n.dist_number AS batch,
           CAST(q.load_date AS DATE) AS as_of_date
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtq_latest/**/*.parquet') q
    LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtn_latest/**/*.parquet') n
      ON n.company = q.company AND n.item_code = q.item_code AND n.sys_number = q.sys_number
    WHERE q.quantity > 0
),
as_of AS (
    SELECT MAX(as_of_date) AS as_of_date FROM batches
),
horizon AS (
    SELECT COALESCE(MAX(value_num) FILTER (WHERE company = '*'), 90) AS days
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'setting' AND param_key = 'expiry_horizon_days'
),
costs AS (
    SELECT company, item_code, warehouse, MAX(avg_price) AS unit_cost
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
    GROUP BY 1, 2, 3
),
currencies AS (
    SELECT company, MAX(local_currency) AS local_currency
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
    GROUP BY 1
),
keys AS (
    SELECT company, item_code, item_key
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet')
),
pace AS (
    SELECT l.company, l.item_code, SUM(COALESCE(l.quantity, 0)) / 90.0 AS daily_sales
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet') l, as_of a
    WHERE l.canceled = 'N' AND l.item_code IS NOT NULL
      AND CAST(l.doc_date AS DATE) > a.as_of_date - INTERVAL 90 DAY
      AND CAST(l.doc_date AS DATE) <= a.as_of_date
    GROUP BY 1, 2
),
pace_by_key AS (
    SELECT k.item_key, p.company, p.daily_sales
    FROM pace p JOIN keys k ON k.company = p.company AND k.item_code = p.item_code
),
ranked AS (
    SELECT b.*,
           a.as_of_date AS snapshot_date,
           COALESCE(p.daily_sales, 0) AS daily_sales,
           COALESCE(k.item_key, 'CODE:' || b.item_code) AS item_key,
           SUM(CASE WHEN b.exp_date IS NULL OR b.exp_date >= a.as_of_date THEN b.qty ELSE 0 END) OVER (
               PARTITION BY b.company, b.item_code
               ORDER BY b.exp_date NULLS LAST, b.sys_number, b.warehouse
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) AS cumulative_qty
    FROM batches b
    CROSS JOIN as_of a
    LEFT JOIN pace p ON p.company = b.company AND p.item_code = b.item_code
    LEFT JOIN keys k ON k.company = b.company AND k.item_code = b.item_code
),
scored AS (
    SELECT r.*,
           CASE WHEN r.exp_date IS NULL THEN NULL ELSE DATEDIFF('day', r.snapshot_date, r.exp_date) END AS days_to_expiry,
           CASE WHEN r.exp_date IS NULL THEN 0
                WHEN r.exp_date < r.snapshot_date THEN r.qty
                ELSE GREATEST(0, LEAST(r.qty, r.cumulative_qty - r.daily_sales * DATEDIFF('day', r.snapshot_date, r.exp_date)))
           END AS at_risk_qty
    FROM ranked r
)
SELECT
    s.company,
    s.warehouse,
    s.item_code,
    s.item_key,
    s.batch,
    s.sys_number,
    s.snapshot_date                                                 AS as_of_date,
    s.exp_date,
    s.days_to_expiry,
    CASE WHEN s.exp_date IS NULL THEN 'sin_caducidad'
         WHEN s.days_to_expiry < 0 THEN 'vencido'
         WHEN s.days_to_expiry <= 30 THEN '0-30'
         WHEN s.days_to_expiry <= 60 THEN '31-60'
         WHEN s.days_to_expiry <= 90 THEN '61-90'
         WHEN s.days_to_expiry <= 180 THEN '91-180'
         ELSE '>180' END                                            AS bucket,
    s.exp_date IS NOT NULL AND s.days_to_expiry <= (SELECT days FROM horizon) AS within_horizon,
    cur.local_currency,
    ROUND(s.qty, 6)                                                 AS qty,
    ROUND(COALESCE(c.unit_cost, 0), 6)                              AS unit_cost_local,
    ROUND(s.qty * COALESCE(c.unit_cost, 0), 2)                      AS value_local,
    ROUND(s.daily_sales, 6)                                         AS daily_sales_90d,
    ROUND(s.at_risk_qty, 6)                                         AS at_risk_qty,
    ROUND(s.at_risk_qty * COALESCE(c.unit_cost, 0), 2)              AS at_risk_value_local,
    (
        SELECT o.company FROM pace_by_key o
        WHERE o.item_key = s.item_key AND o.company <> s.company AND o.daily_sales > s.daily_sales
        ORDER BY o.daily_sales DESC, o.company LIMIT 1
    )                                                               AS transfer_candidate
FROM scored s
LEFT JOIN costs c ON c.company = s.company AND c.item_code = s.item_code AND c.warehouse = s.warehouse
LEFT JOIN currencies cur ON cur.company = s.company
ORDER BY s.company, s.item_code, s.exp_date NULLS LAST, s.warehouse, s.sys_number
