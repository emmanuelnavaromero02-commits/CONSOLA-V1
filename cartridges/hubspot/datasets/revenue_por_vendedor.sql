-- revenue_por_vendedor  (gold)  cartridge: hubspot
-- sources: ["gold/hubspot/pipeline_salud"]
-- description: Revenue cerrado por vendedor y mes: ganado vs. perdido, ticket promedio y tasa de ganados (win rate). Cuelga de la espina pipeline_salud.

SELECT
    mes_cierre                                                          AS mes,
    owner_id,
    vendedor,
    COUNT(*)             FILTER (WHERE estado = 'ganado')               AS deals_ganados,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'ganado'), 2)           AS monto_ganado_usd,
    COUNT(*)             FILTER (WHERE estado = 'perdido')              AS deals_perdidos,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'perdido'), 2)          AS monto_perdido_usd,
    ROUND(AVG(monto_usd) FILTER (WHERE estado = 'ganado'), 2)           AS ticket_promedio_usd,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE estado = 'ganado')
        / NULLIF(COUNT(*) FILTER (WHERE estado IN ('ganado', 'perdido')), 0),
        1)                                                              AS tasa_ganados_pct
-- NOTA: filtramos por estado cerrado (no por mes_cierre IS NOT NULL) para no
-- perder negocio cerrado sin closedate. mes=NULL agrupa esos casos.
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE estado IN ('ganado', 'perdido')
GROUP BY mes, owner_id, vendedor
ORDER BY mes DESC NULLS LAST, monto_ganado_usd DESC NULLS LAST
