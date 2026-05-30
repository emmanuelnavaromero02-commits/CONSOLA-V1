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
-- NOTA: filtramos por estado (no por mes_cierre IS NOT NULL) para NO perder
-- deals abiertos sin fecha de cierre esperada (closedate NULL es común en
-- HubSpot). Esos caen en el grupo mes=NULL ("sin fecha esperada") en lugar de
-- desaparecer, y el forecast total reconcilia con pipeline_salud.
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE estado IN ('forecast', 'ganado')
GROUP BY mes, owner_id, vendedor
ORDER BY mes DESC NULLS LAST, forecast_ponderado_usd DESC NULLS LAST
