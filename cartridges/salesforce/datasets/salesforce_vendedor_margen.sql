-- salesforce_vendedor_margen  (gold)  cartridge: salesforce
-- sources: ["silver/salesforce/salesforce_opportunity_latest", "silver/salesforce/salesforce_opportunitylineitem_latest", "silver/salesforce/salesforce_user_latest"]
-- description: Por vendedor: monto ganado y descuento promedio (PROXY de margen; no hay costo cargado).
WITH won AS (
    SELECT opportunity_id, owner_id FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunity_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
    WHERE is_won
),
li AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunitylineitem_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
),
usr AS (
    SELECT user_id, user_name FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_user_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
)
SELECT
    COALESCE(u.user_name, '(sin dueño)')                                          AS vendedor,
    w.owner_id,
    COUNT(DISTINCT w.opportunity_id)                                              AS deals_ganados,
    ROUND(SUM(li.total_price), 2)                                                 AS monto_ganado_usd,
    ROUND(AVG(1 - li.total_price / NULLIF(li.list_price * li.quantity, 0)) * 100, 1) AS descuento_promedio_pct
FROM won w
JOIN li ON w.opportunity_id = li.opportunity_id
LEFT JOIN usr u ON w.owner_id = u.user_id
GROUP BY 1, 2
ORDER BY monto_ganado_usd DESC NULLS LAST
