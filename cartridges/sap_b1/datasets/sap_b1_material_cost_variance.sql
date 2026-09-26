-- sap_b1_material_cost_variance  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ap_invoice_lines", "silver/sap_b1/sap_b1_ap_credit_memo_lines", "silver/sap_b1/sap_b1_items", "silver/sap_b1/sap_b1_itt1_latest", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Real purchase cost against the item's cost in Business One per company, item and month, in the company's local_currency: units and value bought on supplier invoices net of the footer discount and of supplier credit memos, the real unit price, the item's cost as registered in Business One at the snapshot (its standard cost when the item is valued at standard, its average cost otherwise), the variance in percent and in money, whether it exceeds the threshold cost_variance_max_pct (5 by default), and whether the item is a raw material (a bill-of-materials component); cancelled documents are excluded.
-- partition_by: period

WITH lines AS (
    SELECT company, doc_month, local_currency, item_code, card_code, COALESCE(quantity, 0) AS qty, amount_local_net AS amount
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_invoice_lines/**/*.parquet')
    WHERE canceled = 'N' AND item_code IS NOT NULL
    UNION ALL
    SELECT company, doc_month, local_currency, item_code, card_code, -COALESCE(quantity, 0), -amount_local_net
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N' AND item_code IS NOT NULL
),
bought AS (
    SELECT company, doc_month, local_currency, item_code,
           SUM(qty) AS qty, SUM(amount) AS amount, COUNT(DISTINCT card_code) AS suppliers
    FROM lines
    GROUP BY 1, 2, 3, 4
),
thresholds AS (
    SELECT company, period, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'threshold' AND param_key = 'cost_variance_max_pct'
),
components AS (
    SELECT DISTINCT company, code AS item_code
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_itt1_latest/**/*.parquet')
),
measured AS (
    SELECT b.*, i.item_name, i.item_group_name, i.avg_price AS standard_cost,
           CASE WHEN b.qty <> 0 THEN b.amount / b.qty END AS real_unit_price,
           COALESCE((SELECT t.value_num FROM thresholds t
                      WHERE t.company IN (b.company, '*') AND t.period IN (strftime(b.doc_month, '%Y-%m'), '*')
                      ORDER BY t.company = '*', t.period = '*' LIMIT 1), 5) AS max_variance_pct,
           c.item_code IS NOT NULL AS is_raw_material
    FROM bought b
    LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_items/**/*.parquet') i
      ON i.company = b.company AND i.item_code = b.item_code
    LEFT JOIN components c ON c.company = b.company AND c.item_code = b.item_code
)
SELECT
    company,
    doc_month,
    strftime(doc_month, '%Y-%m')                                        AS period,
    local_currency,
    item_code,
    item_name,
    item_group_name,
    is_raw_material,
    suppliers,
    ROUND(qty, 6)                                                       AS purchased_qty,
    ROUND(amount, 2)                                                    AS purchased_value_local,
    ROUND(real_unit_price, 6)                                           AS real_unit_price_local,
    ROUND(standard_cost, 6)                                             AS standard_cost_local,
    CASE WHEN standard_cost > 0 AND real_unit_price IS NOT NULL
         THEN ROUND(100.0 * (real_unit_price - standard_cost) / standard_cost, 4) END AS variance_pct,
    CASE WHEN real_unit_price IS NOT NULL AND standard_cost IS NOT NULL
         THEN ROUND((real_unit_price - standard_cost) * qty, 2) END   AS variance_value_local,
    max_variance_pct,
    standard_cost > 0 AND real_unit_price IS NOT NULL
      AND abs(100.0 * (real_unit_price - standard_cost) / standard_cost) > max_variance_pct AS above_threshold
FROM measured
ORDER BY company, doc_month, item_code
