-- sap_b1_data_quality  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_business_partners", "silver/sap_b1/sap_b1_ar_invoice_lines", "silver/sap_b1/sap_b1_ap_invoice_lines", "silver/sap_b1/sap_b1_items", "silver/sap_b1/sap_b1_journal_lines", "silver/sap_b1/sap_b1_oact_latest", "silver/sap_b1/sap_b1_obtq_latest", "silver/sap_b1/sap_b1_obtn_latest", "silver/sap_b1/sap_b1_stock_on_hand", "silver/sap_b1/sap_b1_business_parameters"]
-- description: Data quality per company and check: customers with a valid tax id (RFC), documents whose customer, item and account exist in their masters, item lines that carry a cost, batch stock with an expiry date and matching the item stock, intercompany sales matched by the buyer's purchases, and parameters naming a known company; each check against its minimum (threshold dq_min_pct, 95 by default), relaciones_completas sums the relation checks.

WITH params AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
),
partners AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_partners/**/*.parquet')
),
items AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_items/**/*.parquet')
),
invoices AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ar_invoice_lines/**/*.parquet') WHERE canceled = 'N'
),
purchases AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_ap_invoice_lines/**/*.parquet') WHERE canceled = 'N'
),
journal AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_journal_lines/**/*.parquet')
),
chart AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_oact_latest/**/*.parquet')
),
batch_stock AS (
    SELECT q.company, q.item_code, q.whs_code, q.sys_number, q.quantity, n.exp_date
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtq_latest/**/*.parquet') q
    LEFT JOIN read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_obtn_latest/**/*.parquet') n
      ON n.company = q.company AND n.item_code = q.item_code AND n.sys_number = q.sys_number
    WHERE q.quantity > 0
),
stock AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_stock_on_hand/**/*.parquet')
),
known_companies AS (
    SELECT DISTINCT company FROM partners
),
checks AS (
    SELECT company, 'rfc_clientes_valido' AS check_code, 'maestros' AS check_group,
           COUNT(*) AS total,
           COUNT(*) FILTER (WHERE NOT COALESCE(regexp_full_match(regexp_replace(upper(COALESCE(rfc, '')), '[^A-Z0-9Ñ&]', '', 'g'), '[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}'), FALSE)) AS failing
    FROM partners WHERE card_type = 'C' AND NOT is_intercompany GROUP BY 1
    UNION ALL
    SELECT i.company, 'facturas_cliente_en_maestro', 'relaciones',
           COUNT(*), COUNT(*) FILTER (WHERE p.card_code IS NULL)
    FROM invoices i LEFT JOIN partners p ON p.company = i.company AND p.card_code = i.card_code GROUP BY 1
    UNION ALL
    SELECT i.company, 'facturas_articulo_en_maestro', 'relaciones',
           COUNT(*), COUNT(*) FILTER (WHERE it.item_code IS NULL)
    FROM invoices i LEFT JOIN items it ON it.company = i.company AND it.item_code = i.item_code
    WHERE i.item_code IS NOT NULL GROUP BY 1
    UNION ALL
    SELECT j.company, 'asientos_cuenta_en_catalogo', 'relaciones',
           COUNT(*), COUNT(*) FILTER (WHERE c.acct_code IS NULL)
    FROM journal j LEFT JOIN chart c ON c.company = j.company AND c.acct_code = j.account GROUP BY 1
    UNION ALL
    SELECT i.company, 'lineas_articulo_con_costo', 'costos',
           COUNT(*), COUNT(*) FILTER (WHERE COALESCE(i.cost_local, 0) <= 0)
    FROM invoices i JOIN items it ON it.company = i.company AND it.item_code = i.item_code
    WHERE it.inventory_item GROUP BY 1
    UNION ALL
    SELECT company, 'lotes_con_caducidad', 'lotes',
           COUNT(*), COUNT(*) FILTER (WHERE exp_date IS NULL)
    FROM batch_stock GROUP BY 1
    UNION ALL
    SELECT s.company, 'lotes_cuadran_con_existencia', 'lotes',
           COUNT(*), COUNT(*) FILTER (WHERE abs(s.on_hand - COALESCE(b.qty, 0)) > 0.001)
    FROM stock s
    LEFT JOIN (SELECT company, item_code, whs_code, SUM(quantity) AS qty FROM batch_stock GROUP BY 1, 2, 3) b
      ON b.company = s.company AND b.item_code = s.item_code AND b.whs_code = s.warehouse
    WHERE s.batch_managed AND s.on_hand > 0 GROUP BY 1
    UNION ALL
    SELECT sold.company, 'intercompania_cuadra', 'intercompania',
           COUNT(*), COUNT(*) FILTER (WHERE abs(sold.amount - COALESCE(bought.amount, 0)) > greatest(1, 0.005 * abs(sold.amount)))
    FROM (
        SELECT company, counterparty_company AS buyer, doc_month, local_currency, SUM(amount_local) AS amount
        FROM invoices WHERE is_intercompany AND counterparty_company IS NOT NULL GROUP BY 1, 2, 3, 4
    ) sold
    LEFT JOIN (
        SELECT company AS buyer, counterparty_company AS seller, doc_month, local_currency, SUM(amount_local) AS amount
        FROM purchases WHERE is_intercompany AND counterparty_company IS NOT NULL GROUP BY 1, 2, 3, 4
    ) bought ON bought.buyer = sold.buyer AND bought.seller = sold.company AND bought.doc_month = sold.doc_month
            AND bought.local_currency = sold.local_currency
    GROUP BY 1
    UNION ALL
    SELECT p.company, 'parametros_empresa_conocida', 'parametros',
           COUNT(*), COUNT(*) FILTER (WHERE k.company IS NULL)
    FROM params p LEFT JOIN known_companies k ON k.company = p.company
    WHERE p.company <> '*' GROUP BY 1
),
with_overall AS (
    SELECT * FROM checks
    UNION ALL
    SELECT company, 'relaciones_completas', 'relaciones', SUM(total), SUM(failing)
    FROM checks WHERE check_group = 'relaciones' GROUP BY 1
),
thresholds AS (
    SELECT company, param_key, value_num FROM params WHERE kind = 'threshold' AND param_key LIKE 'dq_min_pct%'
)
SELECT
    q.company,
    q.check_code,
    q.check_group,
    q.total,
    q.failing,
    CASE WHEN q.total > 0 THEN ROUND(100.0 * (q.total - q.failing) / q.total, 2) END AS pct_ok,
    COALESCE((
        SELECT t.value_num FROM thresholds t
        WHERE t.company IN (q.company, '*') AND t.param_key IN ('dq_min_pct_' || q.check_code, 'dq_min_pct')
        ORDER BY t.param_key = 'dq_min_pct', t.company = '*'
        LIMIT 1
    ), 95)                                                                          AS min_pct,
    CASE WHEN q.total = 0 THEN 'sin_datos'
         WHEN 100.0 * (q.total - q.failing) / q.total >= COALESCE((
             SELECT t.value_num FROM thresholds t
             WHERE t.company IN (q.company, '*') AND t.param_key IN ('dq_min_pct_' || q.check_code, 'dq_min_pct')
             ORDER BY t.param_key = 'dq_min_pct', t.company = '*'
             LIMIT 1
         ), 95) THEN 'ok'
         ELSE 'bajo_umbral' END                                                     AS status
FROM with_overall q
ORDER BY q.company, q.check_group, q.check_code
