-- pipeline_salud  (gold)  cartridge: hubspot
-- sources: ["silver/hubspot/hubspot_deals_latest", "silver/hubspot/hubspot_owners_latest", "silver/hubspot/hubspot_pipelines_latest"]
-- description: ESPINA del CRM. Un row por deal enriquecido (vendedor + etapa + probabilidad). Plan vs. real: estado='forecast' (abierto, aún sin cerrar) | 'ganado' | 'perdido'. monto_ponderado_usd = monto * probabilidad (solo abiertos). El forecast mensual, el revenue por vendedor, la conversión por etapa y los deals estancados cuelgan de aquí.

-- HubSpot manda fechas en DOS formatos: ISO 8601 ('2024-03-01T12:00:00Z') o
-- epoch en milisegundos ('1700000000000'). TRY_CAST(... AS TIMESTAMP) devuelve
-- NULL sobre el epoch-ms, lo que haría que deals con actividad reciente
-- aparezcan como NULL → falsos "estancados" y se caigan del forecast. El
-- COALESCE de abajo intenta primero ISO y, si falla, interpreta epoch-ms.
WITH deals AS (
    SELECT
        hubspot_id                                          AS deal_id,
        dealname,
        dealtype                                            AS tipo_deal,
        pipeline                                            AS pipeline_id,
        dealstage                                           AS etapa_id,
        hubspot_owner_id                                    AS owner_id,
        TRY_CAST(amount AS DOUBLE)                          AS monto_usd,
        TRY_CAST(hs_deal_stage_probability AS DOUBLE)       AS prob_deal,
        LOWER(COALESCE(hs_is_closed, 'false'))             AS is_closed,
        LOWER(COALESCE(hs_is_closed_won, 'false'))         AS is_closed_won,
        COALESCE(
            TRY_CAST(createdate AS TIMESTAMP),
            TRY_CAST(epoch_ms(TRY_CAST(createdate AS BIGINT)) AS TIMESTAMP)
        )                                                   AS fecha_creacion,
        COALESCE(
            TRY_CAST(closedate AS TIMESTAMP),
            TRY_CAST(epoch_ms(TRY_CAST(closedate AS BIGINT)) AS TIMESTAMP)
        )                                                   AS fecha_cierre,
        COALESCE(
            TRY_CAST(hs_lastactivitydate AS TIMESTAMP),
            TRY_CAST(epoch_ms(TRY_CAST(hs_lastactivitydate AS BIGINT)) AS TIMESTAMP)
        )                                                   AS fecha_ultima_actividad
    FROM read_parquet('s3://{bucket}/silver/hubspot/hubspot_deals_latest/data.parquet')
),
owners AS (
    SELECT
        hubspot_id                                          AS owner_id,
        COALESCE(NULLIF(TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')), ''),
                 email, hubspot_id)                         AS vendedor
    FROM read_parquet('s3://{bucket}/silver/hubspot/hubspot_owners_latest/data.parquet')
),
stages AS (
    SELECT
        pipeline_id,
        pipeline_label,
        stage_id,
        stage_label,
        TRY_CAST(stage_display_order AS INTEGER)            AS orden_etapa,
        TRY_CAST(probability AS DOUBLE)                     AS prob_etapa
    FROM read_parquet('s3://{bucket}/silver/hubspot/hubspot_pipelines_latest/data.parquet')
)
SELECT
    DATE_TRUNC('month', d.fecha_cierre)                     AS mes_cierre,
    d.owner_id,
    COALESCE(o.vendedor, d.owner_id, 'N/D')               AS vendedor,
    d.pipeline_id,
    COALESCE(s.pipeline_label, d.pipeline_id, 'N/D')      AS pipeline,
    d.etapa_id,
    COALESCE(s.stage_label, d.etapa_id, 'N/D')            AS etapa,
    s.orden_etapa,
    CASE
        WHEN d.is_closed_won = 'true' THEN 'ganado'
        WHEN d.is_closed     = 'true' THEN 'perdido'
        ELSE 'forecast'
    END                                                     AS estado,
    d.deal_id,
    d.dealname,
    d.tipo_deal,
    -- monto_usd se deja NULL si el amount no es numérico (no se fuerza a 0):
    -- así un deal con amount inválido no cuenta como un deal real de $0 ni
    -- hunde el ticket promedio. El ponderado sí usa 0 para poder sumar.
    ROUND(d.monto_usd, 2)                                  AS monto_usd,
    COALESCE(s.prob_etapa, d.prob_deal, 0)                AS probabilidad,
    ROUND(
        CASE WHEN d.is_closed = 'true' OR d.is_closed_won = 'true' THEN 0
             ELSE COALESCE(d.monto_usd, 0) * COALESCE(s.prob_etapa, d.prob_deal, 0)
        END, 2)                                            AS monto_ponderado_usd,
    d.fecha_creacion,
    d.fecha_cierre,
    d.fecha_ultima_actividad,
    -- Referencia fijada a UTC para que el umbral de "estancado" (14 días) no
    -- dependa de la zona horaria de la sesión DuckDB (evita drift de ±1 día).
    CASE
        WHEN d.fecha_ultima_actividad IS NULL THEN NULL
        ELSE DATE_DIFF('day', d.fecha_ultima_actividad, CAST(now() AT TIME ZONE 'UTC' AS TIMESTAMP))
    END                                                     AS dias_sin_actividad
FROM deals d
LEFT JOIN owners o ON o.owner_id    = d.owner_id
LEFT JOIN stages s ON s.pipeline_id = d.pipeline_id AND s.stage_id = d.etapa_id
