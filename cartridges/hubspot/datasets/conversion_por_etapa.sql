-- conversion_por_etapa  (gold)  cartridge: hubspot
-- sources: ["gold/hubspot/pipeline_salud"]
-- description: Distribución y conversión por etapa de pipeline: deals abiertos y monto ponderado, más ganados/perdidos y tasa de ganados por etapa. Cuelga de la espina pipeline_salud. NOTA: la velocidad real de avance entre etapas requiere historial de cambios de etapa (deal stage history) — fase 2.

-- Agrupamos por (pipeline_id, etapa_id) — el grano canónico — y exponemos las
-- etiquetas como display (MAX). Agrupar por la etiqueta colapsaría etapas
-- distintas que compartan nombre, o dos pipelines con etiquetas iguales.
SELECT
    MAX(pipeline)                                                      AS pipeline,
    MAX(etapa)                                                         AS etapa,
    pipeline_id,
    etapa_id,
    MAX(orden_etapa)                                                   AS orden_etapa,
    COUNT(*)             FILTER (WHERE estado = 'forecast')            AS deals_abiertos,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'forecast'), 2)        AS monto_abierto_usd,
    ROUND(SUM(monto_ponderado_usd) FILTER (WHERE estado = 'forecast'), 2) AS forecast_abierto_usd,
    COUNT(*)             FILTER (WHERE estado = 'ganado')              AS deals_ganados,
    COUNT(*)             FILTER (WHERE estado = 'perdido')             AS deals_perdidos,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE estado = 'ganado')
        / NULLIF(COUNT(*) FILTER (WHERE estado IN ('ganado', 'perdido')), 0),
        1)                                                             AS tasa_ganados_pct
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
GROUP BY pipeline_id, etapa_id
ORDER BY MAX(pipeline), MAX(orden_etapa) NULLS LAST
