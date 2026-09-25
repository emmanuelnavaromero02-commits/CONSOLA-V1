-- sap_b1_kpi_reconciliation  (gold)  cartridge: sap_b1
-- sources: ["silver/sap_b1/sap_b1_finance_manual_run", "silver/sap_b1/sap_b1_business_parameters", "gold/sap_b1/sap_b1_margin_kpis_month"]
-- description: Finance's manual run against the platform, row by row for the five margin indicators: the platform value at the same indicator, company, month, dimension and key (the key matches the B1 code or, failing that, the name), the difference in money and in percent (percentage points for pct indicators), status ok when the difference is below the tolerance (setting reconciliation_tolerance_pct, 1 by default), fuera_tolerancia otherwise, sin_dato_plataforma when the platform has no such row, and solo_plataforma for customers the platform ranks (destructores, top 20 %) that Finance did not list; each row carries the components that explain a difference: gross invoicing, credit memos, footer discounts, applied cost and commission.

WITH fin AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_finance_manual_run/**/*.parquet')
),
ours AS (
    SELECT * FROM read_parquet('s3://{bucket}/gold/sap_b1/sap_b1_margin_kpis_month/**/*.parquet')
),
tolerances AS (
    SELECT company, value_num
    FROM read_parquet('s3://{bucket}/silver/sap_b1/sap_b1_business_parameters/**/*.parquet')
    WHERE kind = 'setting' AND param_key = 'reconciliation_tolerance_pct' AND value_num IS NOT NULL
),
candidates AS (
    SELECT f.*, o.dim_label, o.local_currency, o.value_local, o.value_pct,
           o.invoiced_before_discount_local, o.credit_memos_local, o.footer_discount_local,
           o.cost_net_local, o.commission_local, o.revenue_net_local, o.dim_key AS platform_key,
           ROW_NUMBER() OVER (
               PARTITION BY f.company, f.period, f.indicator, f.dimension, f.dim_key
               ORDER BY (o.dim_key = f.dim_key) DESC NULLS LAST, o.dim_key
           ) AS _pick
    FROM fin f
    LEFT JOIN ours o
      ON o.company = f.company AND o.period = f.period AND o.indicator = f.indicator
     AND o.dimension = f.dimension
     AND (o.dim_key = f.dim_key OR lower(o.dim_label) = lower(f.dim_key))
),
matched AS (
    SELECT c.*,
           CASE WHEN c.unit = 'pct' THEN c.value_pct ELSE c.value_local END AS platform_value,
           COALESCE(
               (SELECT t.value_num FROM tolerances t WHERE t.company IN (c.company, '*') ORDER BY t.company = '*' LIMIT 1),
               1.0
           ) AS tolerance_pct
    FROM candidates c
    WHERE _pick = 1
),
compared AS (
    SELECT *,
           platform_value - finance_value AS delta,
           CASE
               WHEN platform_value IS NULL THEN NULL
               WHEN unit = 'pct' THEN platform_value - finance_value
               WHEN finance_value <> 0 THEN 100.0 * (platform_value - finance_value) / abs(finance_value)
               WHEN platform_value = 0 THEN 0
           END AS delta_pct
    FROM matched
),
listed AS (
    SELECT DISTINCT company, period, indicator FROM fin
    WHERE indicator IN ('destructores', 'concentracion_top20')
),
platform_only AS (
    SELECT o.*
    FROM ours o
    JOIN listed l ON l.company = o.company AND l.period = o.period AND l.indicator = o.indicator
    WHERE o.dimension = 'cliente'
      AND NOT EXISTS (
          SELECT 1 FROM fin f
          WHERE f.company = o.company AND f.period = o.period AND f.indicator = o.indicator
            AND f.dimension = 'cliente' AND (f.dim_key = o.dim_key OR lower(f.dim_key) = lower(o.dim_label))
      )
)
SELECT
    company,
    period,
    doc_month,
    indicator,
    dimension,
    dim_key,
    platform_key,
    dim_label,
    unit,
    local_currency,
    ROUND(finance_value, 2)                          AS finance_value,
    ROUND(platform_value, 2)                         AS platform_value,
    CASE WHEN unit = 'monto' THEN ROUND(delta, 2) END AS delta_monto,
    ROUND(delta_pct, 4)                              AS delta_pct,
    tolerance_pct,
    CASE
        WHEN platform_value IS NULL THEN 'sin_dato_plataforma'
        WHEN abs(delta_pct) < tolerance_pct THEN 'ok'
        ELSE 'fuera_tolerancia'
    END                                              AS status,
    invoiced_before_discount_local                   AS venta_bruta,
    credit_memos_local                               AS devoluciones_nc,
    footer_discount_local                            AS descuentos_pie_factura,
    cost_net_local                                   AS costo_aplicado,
    commission_local                                 AS comision,
    revenue_net_local                                AS venta_neta,
    upload_id,
    uploaded_at
FROM compared
UNION ALL
SELECT
    company, period, doc_month, indicator, dimension, dim_key, dim_key, dim_label,
    CASE WHEN indicator = 'concentracion_top20' THEN 'pct' ELSE 'monto' END,
    local_currency,
    NULL,
    CASE WHEN indicator = 'concentracion_top20' THEN value_pct ELSE value_local END,
    NULL, NULL, NULL,
    'solo_plataforma',
    invoiced_before_discount_local, credit_memos_local, footer_discount_local, cost_net_local,
    commission_local, revenue_net_local, NULL, NULL
FROM platform_only
ORDER BY company, period, indicator, dimension, dim_key
