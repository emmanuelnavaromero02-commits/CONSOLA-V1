-- salesforce_cobertura_cuota  (gold)  cartridge: salesforce
-- sources: ["silver/salesforce/salesforce_opportunity_latest", "silver/salesforce/salesforce_user_latest"]
-- description: Por vendedor: ganado y forecast ponderado del periodo. La cuota no existe como
-- objeto estándar en Sales Cloud, por eso attainment queda NULL (el agente compara vs el equipo).
WITH opp AS (
    SELECT * FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_opportunity_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
),
usr AS (
    SELECT user_id, user_name FROM read_parquet('s3://{bucket}/silver/salesforce/salesforce_user_latest/**/*.parquet',
                               hive_partitioning = true, union_by_name = true)
)
SELECT
    COALESCE(u.user_name, '(sin dueño)')                                  AS vendedor,
    o.owner_id,
    ROUND(SUM(o.amount) FILTER (WHERE o.is_won), 2)                       AS ganado_usd,
    ROUND(SUM(o.amount * o.probability / 100.0) FILTER (WHERE NOT o.is_closed), 2) AS forecast_ponderado_usd,
    CAST(NULL AS DOUBLE)                                                  AS cuota_usd,
    CAST(NULL AS DOUBLE)                                                  AS attainment_pct
FROM opp o
LEFT JOIN usr u ON o.owner_id = u.user_id
GROUP BY 1, 2
ORDER BY ganado_usd DESC NULLS LAST
