-- forecast_mensual  (gold)  cartridge: hubspot
-- sources: ["gold/hubspot/pipeline_salud"]
-- description: Forecast mensual por vendedor: pipeline abierto ponderado (monto*probabilidad) vs. lo ya ganado del mes. Cuelga de la espina pipeline_salud.

SELECT
    mes_cierre                                                          AS mes,
    owner_id,
    vendedor,
    COUNT(*)                 FILTER (WHERE estado = 'forecast')         AS deals_abiertos,
    ROUND(SUM(monto_usd)     FILTER (WHERE estado = 'forecast'), 2)     AS monto_pipeline_usd,
    ROUND(SUM(monto_ponderado_usd) FILTER (WHERE estado = 'forecast'), 2) AS forecast_ponderado_usd,
    COUNT(*)                 FILTER (WHERE estado = 'ganado')           AS deals_ganados,
    ROUND(SUM(monto_usd)     FILTER (WHERE estado = 'ganado'), 2)       AS monto_ganado_usd
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE mes_cierre IS NOT NULL
GROUP BY mes, owner_id, vendedor
ORDER BY mes DESC, forecast_ponderado_usd DESC NULLS LAST
