-- 94_hubspot_datasets_seed.sql
--
-- Register HubSpot medallion datasets in Refinement's datasets catalog.
-- The cartridge ships SQL under cartridges/hubspot/datasets/*.sql; this
-- migration makes those definitions executable in a fresh or upgraded stack.

INSERT INTO datasets (
    name, layer, cartridge, sources, sql_def, description,
    column_mapping, schedule, updated_at, workspace_id
)
VALUES
(
    'hubspot_deals_latest',
    'silver',
    'hubspot',
    '["raw/hubspot/deals"]'::jsonb,
    $sql$
SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, hs_lastmodifieddate DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/deals/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
$sql$,
    'Última foto de cada deal HubSpot, deduplicada por hubspot_id.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'hubspot_companies_latest',
    'silver',
    'hubspot',
    '["raw/hubspot/companies"]'::jsonb,
    $sql$
SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, hs_lastmodifieddate DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/companies/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
$sql$,
    'Última foto de cada empresa/cuenta HubSpot, deduplicada por hubspot_id.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'hubspot_contacts_latest',
    'silver',
    'hubspot',
    '["raw/hubspot/contacts"]'::jsonb,
    $sql$
SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, lastmodifieddate DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/contacts/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
$sql$,
    'Última foto de cada contacto HubSpot, deduplicada por hubspot_id.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'hubspot_line_items_latest',
    'silver',
    'hubspot',
    '["raw/hubspot/line_items"]'::jsonb,
    $sql$
SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, hs_lastmodifieddate DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/line_items/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
$sql$,
    'Última foto de cada line item HubSpot, deduplicada por hubspot_id.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'hubspot_owners_latest',
    'silver',
    'hubspot',
    '["raw/hubspot/owners"]'::jsonb,
    $sql$
SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY hubspot_id
            ORDER BY load_date DESC, updated_at DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/owners/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
$sql$,
    'Última foto de cada vendedor/dueño HubSpot, deduplicada por hubspot_id.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'hubspot_pipelines_latest',
    'silver',
    'hubspot',
    '["raw/hubspot/pipelines"]'::jsonb,
    $sql$
SELECT * EXCLUDE (_rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY pipeline_id, stage_id
            ORDER BY load_date DESC
        ) AS _rn
    FROM read_parquet(
        's3://{bucket}/raw/hubspot/pipelines/**/*.parquet',
        hive_partitioning = true,
        union_by_name = true
    )
)
WHERE _rn = 1
$sql$,
    'Etapas de pipeline de deals con probabilidad para forecast.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'pipeline_salud',
    'gold',
    'hubspot',
    '["silver/hubspot/hubspot_deals_latest","silver/hubspot/hubspot_owners_latest","silver/hubspot/hubspot_pipelines_latest"]'::jsonb,
    $sql$
WITH deals AS (
    SELECT
        hubspot_id AS deal_id,
        dealname,
        dealtype AS tipo_deal,
        pipeline AS pipeline_id,
        dealstage AS etapa_id,
        hubspot_owner_id AS owner_id,
        TRY_CAST(amount AS DOUBLE) AS monto_usd,
        TRY_CAST(hs_deal_stage_probability AS DOUBLE) AS prob_deal,
        LOWER(COALESCE(hs_is_closed, 'false')) AS is_closed,
        LOWER(COALESCE(hs_is_closed_won, 'false')) AS is_closed_won,
        COALESCE(
            TRY_CAST(createdate AS TIMESTAMP),
            TRY_CAST(epoch_ms(TRY_CAST(createdate AS BIGINT)) AS TIMESTAMP)
        ) AS fecha_creacion,
        COALESCE(
            TRY_CAST(closedate AS TIMESTAMP),
            TRY_CAST(epoch_ms(TRY_CAST(closedate AS BIGINT)) AS TIMESTAMP)
        ) AS fecha_cierre,
        COALESCE(
            TRY_CAST(hs_lastactivitydate AS TIMESTAMP),
            TRY_CAST(epoch_ms(TRY_CAST(hs_lastactivitydate AS BIGINT)) AS TIMESTAMP)
        ) AS fecha_ultima_actividad
    FROM read_parquet('s3://{bucket}/silver/hubspot/hubspot_deals_latest/data.parquet')
),
owners AS (
    SELECT
        hubspot_id AS owner_id,
        COALESCE(NULLIF(TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')), ''),
                 email, hubspot_id) AS vendedor
    FROM read_parquet('s3://{bucket}/silver/hubspot/hubspot_owners_latest/data.parquet')
),
stages AS (
    SELECT
        pipeline_id,
        pipeline_label,
        stage_id,
        stage_label,
        TRY_CAST(stage_display_order AS INTEGER) AS orden_etapa,
        TRY_CAST(probability AS DOUBLE) AS prob_etapa
    FROM read_parquet('s3://{bucket}/silver/hubspot/hubspot_pipelines_latest/data.parquet')
)
SELECT
    DATE_TRUNC('month', d.fecha_cierre) AS mes_cierre,
    d.owner_id,
    COALESCE(o.vendedor, d.owner_id, 'N/D') AS vendedor,
    d.pipeline_id,
    COALESCE(s.pipeline_label, d.pipeline_id, 'N/D') AS pipeline,
    d.etapa_id,
    COALESCE(s.stage_label, d.etapa_id, 'N/D') AS etapa,
    s.orden_etapa,
    CASE
        WHEN d.is_closed_won = 'true' THEN 'ganado'
        WHEN d.is_closed = 'true' THEN 'perdido'
        ELSE 'forecast'
    END AS estado,
    d.deal_id,
    d.dealname,
    d.tipo_deal,
    ROUND(d.monto_usd, 2) AS monto_usd,
    COALESCE(s.prob_etapa, d.prob_deal, 0) AS probabilidad,
    ROUND(
        CASE WHEN d.is_closed = 'true' OR d.is_closed_won = 'true' THEN 0
             ELSE COALESCE(d.monto_usd, 0) * COALESCE(s.prob_etapa, d.prob_deal, 0)
        END, 2
    ) AS monto_ponderado_usd,
    d.fecha_creacion,
    d.fecha_cierre,
    d.fecha_ultima_actividad,
    CASE
        WHEN d.fecha_ultima_actividad IS NULL THEN NULL
        ELSE DATE_DIFF('day', d.fecha_ultima_actividad, CAST(now() AT TIME ZONE 'UTC' AS TIMESTAMP))
    END AS dias_sin_actividad
FROM deals d
LEFT JOIN owners o ON o.owner_id = d.owner_id
LEFT JOIN stages s ON s.pipeline_id = d.pipeline_id AND s.stage_id = d.etapa_id
$sql$,
    'Espina CRM: deals enriquecidos con dueño, etapa, probabilidad y estado.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'forecast_mensual',
    'gold',
    'hubspot',
    '["gold/hubspot/pipeline_salud"]'::jsonb,
    $sql$
SELECT
    mes_cierre AS mes,
    owner_id,
    vendedor,
    COUNT(*) FILTER (WHERE estado = 'forecast') AS deals_abiertos,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'forecast'), 2) AS monto_pipeline_usd,
    ROUND(SUM(monto_ponderado_usd) FILTER (WHERE estado = 'forecast'), 2) AS forecast_ponderado_usd,
    COUNT(*) FILTER (WHERE estado = 'ganado') AS deals_ganados,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'ganado'), 2) AS monto_ganado_usd
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE estado IN ('forecast', 'ganado')
GROUP BY mes, owner_id, vendedor
ORDER BY mes DESC NULLS LAST, forecast_ponderado_usd DESC NULLS LAST
$sql$,
    'Forecast mensual por vendedor desde pipeline_salud.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'revenue_por_vendedor',
    'gold',
    'hubspot',
    '["gold/hubspot/pipeline_salud"]'::jsonb,
    $sql$
SELECT
    mes_cierre AS mes,
    owner_id,
    vendedor,
    COUNT(*) FILTER (WHERE estado = 'ganado') AS deals_ganados,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'ganado'), 2) AS monto_ganado_usd,
    COUNT(*) FILTER (WHERE estado = 'perdido') AS deals_perdidos,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'perdido'), 2) AS monto_perdido_usd,
    ROUND(AVG(monto_usd) FILTER (WHERE estado = 'ganado'), 2) AS ticket_promedio_usd,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE estado = 'ganado')
        / NULLIF(COUNT(*) FILTER (WHERE estado IN ('ganado', 'perdido')), 0),
        1
    ) AS tasa_ganados_pct
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE estado IN ('ganado', 'perdido')
GROUP BY mes, owner_id, vendedor
ORDER BY mes DESC NULLS LAST, monto_ganado_usd DESC NULLS LAST
$sql$,
    'Revenue cerrado por vendedor y mes desde pipeline_salud.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'conversion_por_etapa',
    'gold',
    'hubspot',
    '["gold/hubspot/pipeline_salud"]'::jsonb,
    $sql$
SELECT
    MAX(pipeline) AS pipeline,
    MAX(etapa) AS etapa,
    pipeline_id,
    etapa_id,
    MAX(orden_etapa) AS orden_etapa,
    COUNT(*) FILTER (WHERE estado = 'forecast') AS deals_abiertos,
    ROUND(SUM(monto_usd) FILTER (WHERE estado = 'forecast'), 2) AS monto_abierto_usd,
    ROUND(SUM(monto_ponderado_usd) FILTER (WHERE estado = 'forecast'), 2) AS forecast_abierto_usd,
    COUNT(*) FILTER (WHERE estado = 'ganado') AS deals_ganados,
    COUNT(*) FILTER (WHERE estado = 'perdido') AS deals_perdidos,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE estado = 'ganado')
        / NULLIF(COUNT(*) FILTER (WHERE estado IN ('ganado', 'perdido')), 0),
        1
    ) AS tasa_ganados_pct
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
GROUP BY pipeline_id, etapa_id
ORDER BY MAX(pipeline), MAX(orden_etapa) NULLS LAST
$sql$,
    'Distribución y conversión por etapa de pipeline.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
),
(
    'deals_estancados',
    'gold',
    'hubspot',
    '["gold/hubspot/pipeline_salud"]'::jsonb,
    $sql$
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
    fecha_cierre AS fecha_cierre_esperada,
    fecha_ultima_actividad,
    dias_sin_actividad
FROM read_parquet('s3://{bucket}/gold/hubspot/pipeline_salud/data.parquet')
WHERE estado = 'forecast'
  AND (dias_sin_actividad IS NULL OR dias_sin_actividad >= 14)
ORDER BY dias_sin_actividad DESC NULLS FIRST, monto_usd DESC NULLS LAST
$sql$,
    'Deals abiertos sin actividad reciente desde pipeline_salud.',
    '{}'::jsonb,
    NULL,
    NOW(),
    (SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1)
)
ON CONFLICT (name) DO UPDATE SET
    layer = EXCLUDED.layer,
    cartridge = EXCLUDED.cartridge,
    sources = EXCLUDED.sources,
    sql_def = EXCLUDED.sql_def,
    description = EXCLUDED.description,
    column_mapping = EXCLUDED.column_mapping,
    schedule = EXCLUDED.schedule,
    workspace_id = COALESCE(EXCLUDED.workspace_id, datasets.workspace_id),
    updated_at = NOW();
