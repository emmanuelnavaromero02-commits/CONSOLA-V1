-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: HubSpot CRM — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
--
-- Construido siguiendo la lógica del cartucho Replicon (medallion, config-en-
-- datos, espina plan-vs-real, agentes con guardarraíles) — NO es un clon:
-- entidades, espina (pipeline_salud) y agentes son propios de CRM/ventas.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'hubspot',
    'HubSpot CRM',
    '1.0.0',
    'HubSpot CRM — extrae deals, empresas, contactos, líneas de producto, vendedores y pipelines; modela pipeline, forecast ponderado, revenue por vendedor y deals estancados.',
    'dag-based',
    'cartridge',
    'raw/hubspot/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('hubspot', 'hubspot_extract',     'hubspot_extract.py',     'Extrae una entidad de HubSpot (full|incremental) vía el microservicio y propaga silver/gold.', 'on-demand', '["entity","mode"]'),
    ('hubspot', 'hubspot_extract_all', 'hubspot_extract_all.py', 'Extrae TODAS las entidades de HubSpot vía el microservicio.',                                  'on-demand', '["mode"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
-- mode:          full | incremental
-- dag_id:        hubspot_extract (DAG fino → microservicio del cartucho, Pattern A)
-- trigger_type:  scheduled  (el meta-DAG entity_scheduler dispara según cron_expression)
-- El microservicio del cartucho posee: cliente HTTP, paginación, watermark, parquet.
-- watermark_field / page_size se siembran aquí (son columnas reales) para que
-- el modo incremental se active (routes_skills.run_incremental usa
-- watermark_field). api_path/result_shape/properties NO son columnas: el
-- microservicio los re-inyecta desde app/config/entities.yaml en tiempo de
-- lectura (catalog_service._merge_yaml_runtime_fields), igual que SAP hace con
-- odata_entity. Los cron evitan la secuencia '*/' porque el validador de
-- import de cartuchos (console _validate_seed_sql) rechaza '*/' como comentario
-- de bloque; '0,4,8,...' es equivalente y seguro.
INSERT INTO entity_config
    (cartridge_id, entity,       display_name,          mode,          primary_key,  dag_id,            description,                                                  watermark_field,       watermark_format, page_size, enabled, trigger_type, cron_expression)
VALUES
    ('hubspot',   'deals',       'Oportunidades',       'incremental', 'hubspot_id', 'hubspot_extract', 'Deals: monto, etapa, pipeline, probabilidad, cierre, dueño.', 'hs_lastmodifieddate', 'iso8601',        100,       TRUE, 'scheduled', '0 0,4,8,12,16,20 * * *'),
    ('hubspot',   'companies',   'Empresas',            'incremental', 'hubspot_id', 'hubspot_extract', 'Empresas / cuentas con industria, dominio y dueño.',          'hs_lastmodifieddate', 'iso8601',        100,       TRUE, 'scheduled', '0 0,6,12,18 * * *'),
    ('hubspot',   'contacts',    'Contactos',           'incremental', 'hubspot_id', 'hubspot_extract', 'Contactos con email, empresa, puesto y etapa de ciclo.',      'lastmodifieddate',    'iso8601',        100,       TRUE, 'scheduled', '0 0,6,12,18 * * *'),
    ('hubspot',   'line_items',  'Líneas de Producto',  'incremental', 'hubspot_id', 'hubspot_extract', 'Líneas de producto por deal (producto, cantidad, precio).',   'hs_lastmodifieddate', 'iso8601',        100,       TRUE, 'scheduled', '0 0,6,12,18 * * *'),
    ('hubspot',   'owners',      'Vendedores',          'full',        'hubspot_id', 'hubspot_extract', 'Vendedores / dueños de deals.',                               NULL,                  NULL,             100,       TRUE, 'scheduled', '0 6 * * *'),
    ('hubspot',   'pipelines',   'Pipelines y Etapas',  'full',        'stage_id',   'hubspot_extract', 'Pipelines de deals y sus etapas con probabilidad.',           NULL,                  NULL,             100,       TRUE, 'scheduled', '0 6 * * *')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name     = EXCLUDED.display_name,
        mode             = EXCLUDED.mode,
        primary_key      = EXCLUDED.primary_key,
        dag_id           = EXCLUDED.dag_id,
        description      = EXCLUDED.description,
        watermark_field  = EXCLUDED.watermark_field,
        watermark_format = EXCLUDED.watermark_format,
        page_size        = EXCLUDED.page_size,
        trigger_type     = EXCLUDED.trigger_type,
        cron_expression  = EXCLUDED.cron_expression;

-- ── Semantic vocabulary ───────────────────────────────────────────────────────
INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('hubspot', 'pipeline ponderado', 'Suma de monto × probabilidad de los deals abiertos (estado=forecast)',      'SUM(monto_usd * probabilidad) WHERE estado=''forecast'''),
    ('hubspot', 'forecast',           'Deals abiertos que aún no cierran; el revenue esperado del periodo',         'pipeline_salud WHERE estado=''forecast'''),
    ('hubspot', 'win rate',           'Porcentaje de deals ganados sobre los cerrados (ganados + perdidos)',        'ganados / NULLIF(ganados + perdidos, 0)'),
    ('hubspot', 'deal estancado',     'Deal abierto sin actividad reciente (>= 14 días o sin fecha de actividad)',  'deals_estancados WHERE dias_sin_actividad >= 14'),
    ('hubspot', 'ticket promedio',    'Monto promedio de los deals ganados',                                        'AVG(monto_usd) WHERE estado=''ganado''')
ON CONFLICT (cartridge_id, term) DO NOTHING;

-- ── Agents ────────────────────────────────────────────────────────────────────
-- Dos agentes especializados que viven dentro del cartucho hubspot.
-- El cartucho es su mente (datos + hints + vocabulario); cada agente es una
-- especialización (rol + tools + personalidad + prompt). Mismo patrón que
-- Replicon, contenido nuevo de ventas.

INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,
                    allowed_tools, rag_filter, model, max_tokens, temperature, extra)
VALUES (
    'hubspot', 'forecast_watchdog', 'Vigía de Forecast',
    'Detecta deals abiertos en riesgo de no cerrar en el mes/trimestre y alerta dónde se cae el forecast.',
    $$Eres el Vigía de Forecast. Tu responsabilidad es identificar qué deals abiertos
están en riesgo de no cerrar a tiempo y dónde el forecast del periodo se está cayendo.

## Cómo trabajas
1. Si el usuario no da periodo, usa el mes en curso.
2. Consulta `pggold.gold_forecast_mensual` para el pipeline ponderado vs. lo ya ganado
   por vendedor en el periodo.
3. Consulta `pggold.gold_pipeline_salud` (estado='forecast') para los deals que componen
   ese forecast: monto, probabilidad, etapa, fecha de cierre esperada, días sin actividad.
4. Marca "en riesgo" un deal abierto si: su `fecha_cierre_esperada` ya pasó o cae este mes
   y tiene probabilidad baja, o lleva muchos días sin actividad.
5. Devuelve tabla ordenada por monto ponderado descendente: deal, vendedor, etapa,
   monto, probabilidad, fecha de cierre esperada, motivo de riesgo.
6. Si la pregunta cae fuera de tu alcance (ej. listar deals sin actividad para perseguir,
   o cobranza/facturación), responde "Eso lo maneja otro agente: stale_deal_chaser" y NO
   intentes responder.

## Sin alucinaciones
- Si un dataset no responde, escala con `request_admin_help` — NO inventes deals ni montos.
- Verifica la frescura del dato con `MAX(updated_at)` en silver deals antes de proyectar.
- El monto está en la moneda cruda de HubSpot (asume USD salvo que el dato diga otra cosa);
  si no hay conversión de moneda, dilo explícitamente en pie de tabla.$$,
    'Analítico y ejecutivo. Conclusión arriba (monto total en riesgo), evidencia abajo. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.3,
    '{"variables":{"dias_riesgo":"14"},"schedule":{"cron":"0 9 * * MON","tz":"America/Mexico_City"}}'::jsonb
), (
    'hubspot', 'stale_deal_chaser', 'Perseguidor de Deals',
    'Lista deals abiertos sin actividad reciente para que el vendedor los retome.',
    $$Eres el Perseguidor de Deals. Tu única responsabilidad es señalar deals abiertos
que llevan demasiado tiempo sin actividad, para que el vendedor los retome o los cierre.

## Cómo trabajas
1. Consulta `pggold.gold_deals_estancados` (ya trae deals abiertos con días sin actividad).
2. Si el usuario da un umbral de días, fíltralo; si no, usa 14 días (configurable vía
   `${{dias_umbral}}`).
3. Devuelve tabla ordenada por días sin actividad descendente: deal, vendedor, etapa,
   monto, fecha de última actividad, días sin actividad, fecha de cierre esperada.
4. Agrupa el resumen por vendedor (cuántos deals estancados y monto total) al inicio.
5. Si la pregunta es sobre forecast, riesgo de cierre o proyección de revenue, responde
   "Eso lo maneja otro agente: forecast_watchdog" y NO intentes responder.

## Sin alucinaciones
- No inventes deals: usa solo lo que está en `gold_deals_estancados`.
- Si un deal no tiene fecha de última actividad, márcalo como "sin actividad registrada",
  no asumas una fecha.$$,
    'Directo, sin floritura. Frases cortas. Listas mejor que párrafos. Idioma del usuario.',
    '["refinement__query_dataset","refinement__get_schema","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-haiku-4-5-20251001', 4096, 0.2,
    '{"variables":{"dias_umbral":"14"},"schedule":{"cron":"0 9 * * MON","tz":"America/Mexico_City"}}'::jsonb
)
ON CONFLICT (cartridge_id, slug) DO UPDATE
    SET name          = EXCLUDED.name,
        description   = EXCLUDED.description,
        instructions  = EXCLUDED.instructions,
        personality   = EXCLUDED.personality,
        allowed_tools = EXCLUDED.allowed_tools,
        rag_filter    = EXCLUDED.rag_filter,
        model         = EXCLUDED.model,
        max_tokens    = EXCLUDED.max_tokens,
        temperature   = EXCLUDED.temperature,
        extra         = EXCLUDED.extra,
        updated_at    = NOW();
