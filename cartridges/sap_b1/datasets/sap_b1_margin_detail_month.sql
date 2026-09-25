-- sap_b1_margin_detail_month  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ar_credit_memo_lines", "silver/sap_b1/sap_b1_customer_crosswalk", "silver/sap_b1/sap_b1_item_crosswalk", "silver/sap_b1/sap_b1_business_partners", "silver/sap_b1/sap_b1_items", "silver/sap_b1/sap_b1_oslp_latest"]
-- description: Margin per company, month, customer, item, channel (the customer's business partner group) and sales employee, in the company's local_currency: invoice amounts before and after the document (footer) discount, credit memos, net revenue, the cost the documents carry (B1's cost at posting, the standard cost for items valued at standard), gross margin, the sales commission on the lines and the contribution margin (gross margin minus commission); cancelled documents and their cancellation documents are excluded.
-- partition_by: period

WITH lines AS (
    SELECT company, doc_month, local_currency, card_code, card_name, is_intercompany, slp_code, item_code,
           item_description, quantity, amount_local, amount_local_net, COALESCE(cost_local, 0) AS cost_local,
           commission_local, 'invoice' AS kind
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet')
    WHERE canceled = 'N'
    UNION ALL
    SELECT company, doc_month, local_currency, card_code, card_name, is_intercompany, slp_code, item_code,
           item_description, quantity, amount_local, amount_local_net, COALESCE(cost_local, 0) AS cost_local,
           commission_local, 'credit' AS kind
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_credit_memo_lines/**/*.parquet')
    WHERE canceled = 'N'
),
grouped AS (
    SELECT company, doc_month, local_currency, card_code, is_intercompany, slp_code, item_code,
           MAX(card_name)                                                                  AS card_name,
           MAX(item_description)                                                           AS item_description,
           SUM(CASE WHEN kind = 'invoice' THEN amount_local ELSE 0 END)                    AS invoiced_before_discount,
           SUM(CASE WHEN kind = 'invoice' THEN amount_local - amount_local_net ELSE 0 END) AS footer_discount,
           SUM(CASE WHEN kind = 'invoice' THEN amount_local_net ELSE 0 END)                AS invoiced,
           SUM(CASE WHEN kind = 'credit' THEN amount_local_net ELSE 0 END)                 AS credited,
           SUM(CASE WHEN kind = 'invoice' THEN COALESCE(quantity, 0) ELSE -COALESCE(quantity, 0) END) AS quantity_net,
           SUM(CASE WHEN kind = 'invoice' THEN cost_local ELSE -cost_local END)            AS cost_net,
           SUM(CASE WHEN kind = 'invoice' THEN commission_local ELSE -commission_local END) AS commission_net
    FROM lines
    GROUP BY 1, 2, 3, 4, 5, 6, 7
),
partners AS (
    SELECT company, card_code, group_code, group_name
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_partners/**/*.parquet')
),
items AS (
    SELECT company, item_code, item_name, item_group_code, item_group_name
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_items/**/*.parquet')
),
sellers AS (
    SELECT company, slp_code, slp_name
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oslp_latest/**/*.parquet')
)
SELECT
    g.company,
    g.doc_month,
    strftime(g.doc_month, '%Y-%m')                                                        AS period,
    CASE WHEN g.is_intercompany THEN 'intercompany' ELSE 'external' END                   AS scope,
    g.local_currency,
    g.card_code,
    g.card_name,
    COALESCE(cx.customer_key, g.company || ':' || g.card_code)                            AS customer_key,
    COALESCE(CAST(bp.group_code AS VARCHAR), 'sin_grupo')                                  AS channel_code,
    COALESCE(bp.group_name, 'Sin grupo')                                                   AS channel_name,
    g.slp_code,
    COALESCE(s.slp_name, CASE WHEN g.slp_code IS NULL OR g.slp_code < 0 THEN 'Sin vendedor' ELSE 'Vendedor ' || CAST(g.slp_code AS VARCHAR) END) AS slp_name,
    COALESCE(g.item_code, '')                                                              AS item_code,
    COALESCE(it.item_name, g.item_description, 'Sin artículo')                             AS item_name,
    COALESCE(ix.item_key, CASE WHEN g.item_code IS NULL THEN NULL ELSE g.company || ':' || g.item_code END) AS item_key,
    it.item_group_code,
    COALESCE(it.item_group_name, CASE WHEN g.item_code IS NULL THEN 'Servicios y ajustes' ELSE 'Sin grupo' END) AS item_group_name,
    ROUND(g.quantity_net, 6)                                                               AS quantity_net,
    ROUND(g.invoiced_before_discount, 6)                                                   AS invoiced_before_discount_local,
    ROUND(g.footer_discount, 6)                                                            AS footer_discount_local,
    ROUND(g.invoiced, 6)                                                                   AS invoiced_local,
    ROUND(g.credited, 6)                                                                   AS credit_memos_local,
    ROUND(g.invoiced - g.credited, 6)                                                      AS revenue_net_local,
    ROUND(g.cost_net, 6)                                                                   AS cost_net_local,
    ROUND(g.invoiced - g.credited - g.cost_net, 6)                                         AS gross_margin_local,
    ROUND(g.commission_net, 6)                                                             AS commission_local,
    ROUND(g.invoiced - g.credited - g.cost_net - g.commission_net, 6)                      AS contribution_margin_local,
    CASE WHEN g.invoiced - g.credited <> 0
         THEN ROUND(100.0 * (g.invoiced - g.credited - g.cost_net) / (g.invoiced - g.credited), 2) END AS gross_margin_pct,
    CASE WHEN g.invoiced - g.credited <> 0
         THEN ROUND(100.0 * (g.invoiced - g.credited - g.cost_net - g.commission_net) / (g.invoiced - g.credited), 2) END AS contribution_margin_pct
FROM grouped g
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_customer_crosswalk/**/*.parquet') cx
  ON cx.company = g.company AND cx.card_code = g.card_code
LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_item_crosswalk/**/*.parquet') ix
  ON ix.company = g.company AND ix.item_code = g.item_code
LEFT JOIN partners bp
  ON bp.company = g.company AND bp.card_code = g.card_code
LEFT JOIN items it
  ON it.company = g.company AND it.item_code = g.item_code
LEFT JOIN sellers s
  ON s.company = g.company AND s.slp_code = g.slp_code
ORDER BY g.company, g.doc_month, scope, g.card_code, item_code, g.slp_code
