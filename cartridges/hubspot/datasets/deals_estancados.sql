-- deals_estancados  (gold)  cartridge: hubspot
-- sources: ["gold/hubspot/pipeline_salud"]
-- description: Deals abiertos (forecast) sin actividad reciente (>= 14 días o sin fecha de actividad). Insumo del agente stale_deal_chaser. Cuelga de la espina pipeline_salud.

SELECT
    deal_id,
    dealname,
    vendedor,
    owner_id,
    pipeline,
    etapa,
    monto_usd,
    probabilidad,
    monto_ponderado_usd,
    fecha_cierre                                                       AS fecha_cierre_esperada,
    fecha_ultima_actividad,
    dias_sin_actividad
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE estado = 'forecast'
  AND (dias_sin_actividad IS NULL OR dias_sin_actividad >= 14)
ORDER BY dias_sin_actividad DESC NULLS FIRST, monto_usd DESC NULLS LAST
