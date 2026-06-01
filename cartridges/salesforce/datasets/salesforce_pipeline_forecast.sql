-- salesforce_pipeline_forecast  (gold)  cartridge: salesforce
-- sources: ["silver/salesforce/salesforce_opportunity_latest", "silver/salesforce/salesforce_user_latest"]
-- description: Por mes de cierre x vendedor: pipeline abierto, forecast ponderado y lo ya ganado.
WITH opp AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunity_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
),
usr AS (
    SELECT user_id, user_name FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_user_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
)
SELECT
    CAST(DATE_TRUNC('month', o.close_date) AS DATE)                         AS mes,
    COALESCE(u.user_name, '(sin dueño)')                                   AS vendedor,
    o.owner_id                                                             AS owner_id,
    COUNT(*) FILTER (WHERE NOT o.is_closed)                                AS deals_abiertos,
    ROUND(SUM(o.amount) FILTER (WHERE NOT o.is_closed), 2)                 AS monto_pipeline_usd,
    ROUND(SUM(o.amount * o.probability / 100.0) FILTER (WHERE NOT o.is_closed), 2) AS forecast_ponderado_usd,
    COUNT(*) FILTER (WHERE o.is_won)                                       AS deals_ganados,
    ROUND(SUM(o.amount) FILTER (WHERE o.is_won), 2)                        AS monto_ganado_usd
FROM opp o
LEFT JOIN usr u ON o.owner_id = u.user_id
WHERE o.close_date IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY mes DESC, forecast_ponderado_usd DESC
