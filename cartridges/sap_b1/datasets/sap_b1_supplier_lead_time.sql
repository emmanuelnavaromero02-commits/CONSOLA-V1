-- sap_b1_supplier_lead_time  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_goods_receipt_lines", "silver/sap_b1/sap_b1_purchase_order_lines", "silver/sap_b1/sap_b1_business_partners", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Supplier delivery against the purchase order per company, supplier, item and month of receipt: goods receipt lines linked to the purchase order line they come from, the days from order to receipt, the days the order promised (its due date), how many receipts arrived after the promised date plus the grace (setting lead_time_tolerance_days, 0 by default), the share on time and the worst delay; group-company suppliers are included and marked.
-- partition_by: period

WITH receipts AS (
    SELECT company, doc_entry, line_num, CAST(doc_date AS DATE) AS receipt_date, card_code, item_code,
           COALESCE(quantity, 0) AS qty, base_entry, base_line, is_intercompany
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_goods_receipt_lines/**/*.parquet')
    WHERE canceled = 'N' AND base_type = 22 AND item_code IS NOT NULL
),
orders AS (
    SELECT company, doc_entry, line_num, CAST(doc_date AS DATE) AS order_date, CAST(doc_due_date AS DATE) AS due_date
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_purchase_order_lines/**/*.parquet')
),
tolerance AS (
    SELECT company, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'setting' AND param_key = 'lead_time_tolerance_days' AND value_num IS NOT NULL
),
linked AS (
    SELECT r.*, o.order_date, o.due_date,
           DATEDIFF('day', o.order_date, r.receipt_date) AS lead_days,
           DATEDIFF('day', o.order_date, o.due_date)     AS promised_days,
           DATEDIFF('day', o.due_date, r.receipt_date)   AS delay_days,
           COALESCE((SELECT t.value_num FROM tolerance t WHERE t.company IN (r.company, '*') ORDER BY t.company = '*' LIMIT 1), 0) AS grace_days
    FROM receipts r
    JOIN orders o ON o.company = r.company AND o.doc_entry = r.base_entry AND o.line_num = r.base_line
)
SELECT
    l.company,
    CAST(DATE_TRUNC('month', l.receipt_date) AS DATE)                   AS doc_month,
    strftime(l.receipt_date, '%Y-%m')                                   AS period,
    l.card_code,
    MAX(bp.card_name)                                                   AS supplier_name,
    bool_or(l.is_intercompany)                                          AS is_intercompany,
    l.item_code,
    COUNT(*)                                                            AS receipts,
    ROUND(SUM(l.qty), 6)                                                AS received_qty,
    ROUND(AVG(l.lead_days), 2)                                          AS avg_lead_days,
    ROUND(AVG(l.promised_days), 2)                                      AS avg_promised_days,
    COUNT(*) FILTER (WHERE l.delay_days > l.grace_days)                 AS late_receipts,
    ROUND(100.0 * COUNT(*) FILTER (WHERE l.delay_days <= l.grace_days) / COUNT(*), 2) AS on_time_pct,
    GREATEST(MAX(l.delay_days), 0)                                      AS max_delay_days,
    MAX(l.grace_days)                                                   AS grace_days
FROM linked l
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_partners/**/*.parquet') bp
  ON bp.company = l.company AND bp.card_code = l.card_code
GROUP BY l.company, 2, 3, l.card_code, l.item_code
ORDER BY l.company, doc_month, l.card_code, l.item_code
