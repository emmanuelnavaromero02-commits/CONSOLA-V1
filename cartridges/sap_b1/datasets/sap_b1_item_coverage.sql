-- sap_b1_item_coverage  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_stock_on_hand", "silver/sap_b1/sap_b1_items", "silver/sap_b1/sap_b1_inventory_movements", "silver/sap_b1/sap_b1_purchase_order_lines", "silver/sap_b1/sap_b1_production_orders", "silver/sap_b1/sap_b1_business_parameters", "silver/sap_b1/sap_b1_itt1_latest", "silver/sap_b1/sap_b1_goods_receipt_lines"]
-- description: Coverage and replenishment per company and inventory item as of the stock snapshot, all warehouses together, in the company's local_currency: available stock (on hand minus committed), open purchase orders (not cancelled, open lines) and open production orders (planned or released, not yet completed or rejected); consumption over the 90 days up to the snapshot (deliveries, invoices and goods issues net of returns; transfers excluded) and the need of the production plan (components still to issue on open production orders due within setting planning_horizon_days, 90 by default); the daily requirement is the larger of the historical pace and the plan's need spread over the horizon (consumption_basis says which); days of coverage with and without the open orders; open purchase orders against the net need over the horizon; raw materials (BOM components) ranked by the value they consume with the top setting critical_materials_top_n (30) marked critical; a traffic light on coverage with orders (rojo below setting coverage_red_days 30, amarillo below coverage_yellow_days 60, verde otherwise, sin_consumo without requirement) and a stockout risk flag when stock with open orders runs out before a new order could arrive; the lead time (the item's, else setting default_lead_time_days, 7 by default), the reorder point and order-up-to level recalculated from the requirement (settings safety_days 7 and review_period_days 14 by default) next to Business One's own minimum and maximum, and, below the reorder point, the suggested quantity up to the order-up-to level, at least the item's minimum order and rounded up to its order multiple, whether to buy or make it, the preferred supplier and an alternate supplier (the most recent other supplier that delivered the item), and the date to place the order by. Read-only: nothing is written back to Business One.

WITH stock_rows AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
),
snapshot AS (
    SELECT company, MAX(CAST(load_date AS DATE)) AS as_of_date, MAX(local_currency) AS local_currency
    FROM stock_rows
    GROUP BY 1
),
stock AS (
    SELECT company, item_code,
           SUM(on_hand) AS on_hand, SUM(COALESCE(committed, 0)) AS committed, SUM(available) AS available,
           SUM(COALESCE(on_order, 0)) AS b1_on_order, SUM(COALESCE(min_stock, 0)) AS b1_min_stock,
           SUM(COALESCE(max_stock, 0)) AS b1_max_stock, MAX(avg_price) AS avg_price
    FROM stock_rows
    GROUP BY 1, 2
),
items AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_items/**/*.parquet')
),
params AS (
    SELECT company, param_key, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'setting' AND param_key IN ('default_lead_time_days', 'safety_days', 'review_period_days',
                                             'planning_horizon_days', 'coverage_red_days', 'coverage_yellow_days',
                                             'critical_materials_top_n')
),
settings AS (
    SELECT s.company,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'default_lead_time_days' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'default_lead_time_days' AND p.company = '*'), 7) AS default_lead_time_days,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'safety_days' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'safety_days' AND p.company = '*'), 7) AS safety_days,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'review_period_days' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'review_period_days' AND p.company = '*'), 14) AS review_period_days,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'planning_horizon_days' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'planning_horizon_days' AND p.company = '*'), 90) AS horizon_days,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'coverage_red_days' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'coverage_red_days' AND p.company = '*'), 30) AS red_days,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'coverage_yellow_days' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'coverage_yellow_days' AND p.company = '*'), 60) AS yellow_days,
           COALESCE(MAX(p.value_num) FILTER (WHERE p.param_key = 'critical_materials_top_n' AND p.company = s.company),
                    MAX(p.value_num) FILTER (WHERE p.param_key = 'critical_materials_top_n' AND p.company = '*'), 30) AS top_n
    FROM snapshot s
    LEFT JOIN params p ON p.company IN (s.company, '*')
    GROUP BY 1
),
consumption AS (
    SELECT m.company, m.item_code, SUM(COALESCE(m.out_qty, 0) - COALESCE(m.in_qty, 0)) AS consumed
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_inventory_movements/**/*.parquet') m
    JOIN snapshot s ON s.company = m.company
    WHERE m.trans_type IN (13, 14, 15, 16, 60)
      AND CAST(m.doc_date AS DATE) > s.as_of_date - INTERVAL 90 DAY
      AND CAST(m.doc_date AS DATE) <= s.as_of_date
    GROUP BY 1, 2
),
open_po AS (
    SELECT company, item_code, SUM(open_qty) AS open_po_qty, COUNT(DISTINCT doc_entry) AS open_po_docs,
           MIN(CAST(doc_due_date AS DATE)) AS next_po_due_date
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_purchase_order_lines/**/*.parquet')
    WHERE canceled = 'N' AND line_status = 'O' AND item_code IS NOT NULL AND open_qty > 0
    GROUP BY 1, 2
),
open_production AS (
    SELECT company, product_item_code AS item_code,
           SUM(GREATEST(planned_qty - COALESCE(completed_qty, 0) - COALESCE(rejected_qty, 0), 0)) AS open_production_qty,
           MIN(CAST(due_date AS DATE)) AS next_production_due_date
    FROM (
        SELECT DISTINCT company, doc_entry, product_item_code, planned_qty, completed_qty, rejected_qty, due_date
        FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_production_orders/**/*.parquet')
        WHERE status IN ('P', 'R')
    )
    GROUP BY 1, 2
),
plan_need AS (
    SELECT o.company, o.component_item_code AS item_code,
           SUM(GREATEST(o.component_planned_qty - COALESCE(o.component_issued_qty, 0), 0)) AS plan_need_qty,
           COUNT(DISTINCT o.doc_entry) AS plan_orders
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_production_orders/**/*.parquet') o
    JOIN snapshot sn ON sn.company = o.company
    JOIN settings se ON se.company = o.company
    WHERE o.status IN ('P', 'R') AND o.component_item_code IS NOT NULL
      AND CAST(o.due_date AS DATE) <= sn.as_of_date + CAST(se.horizon_days AS INTEGER)
    GROUP BY 1, 2
),
bom_components AS (
    SELECT DISTINCT company, code AS item_code
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_itt1_latest/**/*.parquet')
    WHERE code IS NOT NULL
),
receipts AS (
    SELECT company, item_code, card_code, MAX(CAST(doc_date AS DATE)) AS last_receipt
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_goods_receipt_lines/**/*.parquet')
    WHERE canceled = 'N' AND item_code IS NOT NULL
    GROUP BY 1, 2, 3
),
base AS (
    SELECT st.company, st.item_code, i.item_name, i.item_group_name, sn.as_of_date, sn.local_currency,
           CASE WHEN i.procurement_method = 'M' THEN 'producir' ELSE 'comprar' END AS action,
           i.preferred_supplier,
           st.on_hand, st.committed, st.available,
           COALESCE(po.open_po_qty, 0) AS open_po_qty, COALESCE(po.open_po_docs, 0) AS open_po_docs, po.next_po_due_date,
           COALESCE(pr.open_production_qty, 0) AS open_production_qty, pr.next_production_due_date,
           st.b1_on_order, st.b1_min_stock, st.b1_max_stock,
           GREATEST(COALESCE(c.consumed, 0), 0) AS consumed_90d,
           GREATEST(COALESCE(c.consumed, 0), 0) / 90.0 AS historical_daily,
           COALESCE(pn.plan_need_qty, 0) AS plan_need_qty, COALESCE(pn.plan_orders, 0) AS plan_orders,
           COALESCE(pn.plan_need_qty, 0) / se.horizon_days AS plan_daily,
           GREATEST(GREATEST(COALESCE(c.consumed, 0), 0) / 90.0, COALESCE(pn.plan_need_qty, 0) / se.horizon_days) AS daily_consumption,
           CASE WHEN COALESCE(pn.plan_need_qty, 0) / se.horizon_days > GREATEST(COALESCE(c.consumed, 0), 0) / 90.0
                THEN 'plan' ELSE 'historico' END AS consumption_basis,
           se.horizon_days, se.red_days, se.yellow_days, se.top_n,
           bc.item_code IS NOT NULL AS is_raw_material,
           (SELECT r.card_code FROM receipts r
             WHERE r.company = st.company AND r.item_code = st.item_code
               AND r.card_code IS DISTINCT FROM i.preferred_supplier
             ORDER BY r.last_receipt DESC, r.card_code LIMIT 1) AS alternate_supplier,
           COALESCE(i.lead_time_days, se.default_lead_time_days) AS lead_time_days,
           CASE WHEN i.lead_time_days IS NULL THEN 'parametro' ELSE 'articulo' END AS lead_time_source,
           se.safety_days, se.review_period_days,
           COALESCE(i.min_order_qty, 0) AS min_order_qty,
           CASE WHEN COALESCE(i.order_multiple, 0) > 0 THEN i.order_multiple ELSE 1 END AS order_multiple,
           COALESCE(NULLIF(i.last_purchase_price, 0), st.avg_price, 0) AS unit_cost
    FROM stock st
    JOIN snapshot sn ON sn.company = st.company
    JOIN settings se ON se.company = st.company
    LEFT JOIN items i ON i.company = st.company AND i.item_code = st.item_code
    LEFT JOIN consumption c ON c.company = st.company AND c.item_code = st.item_code
    LEFT JOIN open_po po ON po.company = st.company AND po.item_code = st.item_code
    LEFT JOIN open_production pr ON pr.company = st.company AND pr.item_code = st.item_code
    LEFT JOIN plan_need pn ON pn.company = st.company AND pn.item_code = st.item_code
    LEFT JOIN bom_components bc ON bc.company = st.company AND bc.item_code = st.item_code
    WHERE COALESCE(i.inventory_item, true)
),
measured AS (
    SELECT b.*,
           b.available + b.open_po_qty + b.open_production_qty AS position_qty,
           CASE WHEN b.daily_consumption > 0 THEN b.available / b.daily_consumption END AS coverage_days,
           CASE WHEN b.daily_consumption > 0
                THEN (b.available + b.open_po_qty + b.open_production_qty) / b.daily_consumption END AS coverage_with_orders_days,
           b.daily_consumption * (b.lead_time_days + b.safety_days) AS reorder_point,
           b.daily_consumption * (b.lead_time_days + b.safety_days + b.review_period_days) AS order_up_to,
           GREATEST(b.daily_consumption * b.horizon_days - b.available, 0) AS net_need_qty,
           CASE WHEN b.is_raw_material THEN
               ROW_NUMBER() OVER (PARTITION BY b.company, b.is_raw_material
                                  ORDER BY b.daily_consumption * b.unit_cost DESC, b.item_code)
           END AS criticality_rank
    FROM base b
),
suggested AS (
    SELECT m.*,
           CASE WHEN m.daily_consumption = 0 THEN 'sin_consumo'
                WHEN m.coverage_with_orders_days < m.red_days THEN 'rojo'
                WHEN m.coverage_with_orders_days < m.yellow_days THEN 'amarillo'
                ELSE 'verde' END AS coverage_color,
           m.daily_consumption > 0 AND m.coverage_with_orders_days < m.lead_time_days AS stockout_risk,
           CASE WHEN m.daily_consumption > 0 AND m.position_qty < m.reorder_point
                THEN CEIL(GREATEST(m.order_up_to - m.position_qty, m.min_order_qty) / m.order_multiple) * m.order_multiple
                ELSE 0 END AS suggested_qty
    FROM measured m
)
SELECT
    company,
    item_code,
    item_name,
    item_group_name,
    as_of_date,
    local_currency,
    ROUND(on_hand, 6)                                                   AS on_hand,
    ROUND(committed, 6)                                                 AS committed,
    ROUND(available, 6)                                                 AS available,
    ROUND(open_po_qty, 6)                                               AS open_po_qty,
    open_po_docs,
    next_po_due_date,
    ROUND(open_production_qty, 6)                                       AS open_production_qty,
    next_production_due_date,
    ROUND(position_qty, 6)                                              AS position_qty,
    ROUND(b1_on_order, 6)                                               AS b1_on_order,
    ROUND(consumed_90d, 6)                                              AS consumed_90d,
    ROUND(historical_daily, 6)                                          AS historical_daily,
    ROUND(plan_need_qty, 6)                                             AS plan_need_qty,
    plan_orders,
    CAST(horizon_days AS INTEGER)                                       AS planning_horizon_days,
    ROUND(plan_daily, 6)                                                AS plan_daily,
    ROUND(daily_consumption, 6)                                         AS daily_consumption,
    consumption_basis,
    is_raw_material,
    criticality_rank,
    COALESCE(criticality_rank <= top_n, false)                          AS is_critical,
    ROUND(net_need_qty, 6)                                              AS net_need_qty,
    CASE WHEN net_need_qty > 0 THEN ROUND(100.0 * open_po_qty / net_need_qty, 2) END AS open_po_vs_need_pct,
    ROUND(coverage_days, 1)                                             AS coverage_days,
    ROUND(coverage_with_orders_days, 1)                                 AS coverage_with_orders_days,
    CASE WHEN daily_consumption > 0
         THEN as_of_date + CAST(FLOOR(coverage_with_orders_days) AS INTEGER) END AS stockout_date,
    CAST(lead_time_days AS INTEGER)                                     AS lead_time_days,
    lead_time_source,
    CAST(safety_days AS INTEGER)                                        AS safety_days,
    CAST(review_period_days AS INTEGER)                                 AS review_period_days,
    ROUND(reorder_point, 6)                                             AS reorder_point,
    ROUND(order_up_to, 6)                                               AS order_up_to,
    ROUND(b1_min_stock, 6)                                              AS b1_min_stock,
    ROUND(b1_max_stock, 6)                                              AS b1_max_stock,
    ROUND(reorder_point - b1_min_stock, 6)                              AS min_stock_gap,
    coverage_color,
    stockout_risk,
    ROUND(suggested_qty, 6)                                             AS suggested_qty,
    CASE WHEN suggested_qty > 0 THEN action END                         AS suggested_action,
    CASE WHEN suggested_qty > 0 AND action = 'comprar' THEN preferred_supplier END AS suggested_supplier,
    CASE WHEN suggested_qty > 0 AND action = 'comprar' THEN alternate_supplier END AS alternate_supplier,
    CASE WHEN suggested_qty > 0
         THEN as_of_date + CAST(GREATEST(0, FLOOR(coverage_with_orders_days - lead_time_days)) AS INTEGER) END AS order_by_date,
    ROUND(min_order_qty, 6)                                             AS min_order_qty,
    ROUND(order_multiple, 6)                                            AS order_multiple,
    ROUND(unit_cost, 6)                                                 AS unit_cost_local,
    ROUND(suggested_qty * unit_cost, 2)                                 AS suggested_value_local
FROM suggested
ORDER BY company, item_code
