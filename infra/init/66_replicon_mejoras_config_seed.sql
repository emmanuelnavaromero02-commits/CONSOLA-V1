-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: Replicon PSA — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

-- Existing beta installs may contain the previous Replicon cartridge metadata.
-- Refresh the owned operational rows so the MEJORAS Replicon module is the
-- canonical one while preserving connections, run history and raw data.
DELETE FROM cartridge_dags WHERE cartridge_id = 'replicon';
DELETE FROM entity_config WHERE cartridge_id = 'replicon';
DELETE FROM semantic_terms WHERE cartridge_id = 'replicon';

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'replicon',
    'Replicon PSA',
    '3.0.0',
    'Replicon Professional Services Automation — extrae datos de workforce: usuarios, proyectos, tiempo registrado, tareas, clientes, facturas, asignaciones y gastos.',
    'dag-based',
    'cartridge',
    'raw/replicon/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('replicon', 'replicon_extract', 'replicon_extract.py', 'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('replicon', 'file_ingest',      'file_ingest.py',      'Ingesta genérica de archivos (csv/excel) del lake → parquet bronze. Params por entidad en dag_params.', 'on-demand', '["entity","cartridge_id","mode","file_pattern","format","delimiter","encoding","sheet","skiprows","parser"]'),
    ('replicon', 'replicon_ses_inbox_import', 'replicon_ses_inbox_import.py', 'Ingesta adjuntos recibidos por Amazon SES en S3.', 'scheduled', '["bucket","prefix"]'),
    ('replicon', 'replicon_outlook_audit_report_import', 'replicon_outlook_audit_report_import.py', 'Ingesta reporte de auditoría recibido por Outlook.', 'scheduled', '["mailbox","subject_filter"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
-- display_name: nombre legible para UI y reportes
-- mode:         full | incremental
-- dag_id:       DAG que maneja la extracción
-- trigger_type: manual | scheduled
-- The DAG itself owns: connection logic, watermark field, API call, mapping
INSERT INTO entity_config
    (cartridge_id, entity,               display_name,                         mode,          primary_key,        dag_id,              description,                                                   enabled, trigger_type)
VALUES
    ('replicon',   'TimeEntry',          'Registros de Tiempo',                'incremental', 'entry_id',         'replicon_extract',  'Registros de tiempo con horas, estado facturable y aprobación', TRUE, 'manual'),
    ('replicon',   'Timesheet',          'Hojas de Tiempo',                    'incremental', 'timesheet_id',     'replicon_extract',  'Hojas de tiempo con período, usuario y aprobación',             TRUE, 'manual'),
    ('replicon',   'ExpenseEntry',       'Gastos',                             'incremental', 'expense_id',       'replicon_extract',  'Gastos con monto, categoría y flag facturable',                 TRUE, 'manual'),
    ('replicon',   'BillingItem',        'Items de Facturación',               'incremental', 'billing_item_id',  'replicon_extract',  'Items de facturación por proyecto',                             TRUE, 'manual'),
    ('replicon',   'InvoiceItem',        'Items de Factura',                   'incremental', 'invoice_item_id',  'replicon_extract',  'Items de factura con monto, horas y tarifa',                    TRUE, 'manual'),
    ('replicon',   'CostItem',           'Items de Costo',                     'incremental', 'cost_item_id',     'replicon_extract',  'Items de costo por proyecto',                                   TRUE, 'manual'),
    ('replicon',   'ProfitItem',         'Items de Ganancia',                  'incremental', 'profit_item_id',   'replicon_extract',  'Items de ganancia por proyecto',                                TRUE, 'manual'),
    ('replicon',   'User',               'Usuarios',                           'full',        'user_id',          'replicon_extract',  'Usuarios del sistema con costos y tarifas',                     TRUE, 'manual'),
    ('replicon',   'Client',             'Clientes',                           'full',        'client_id',        'replicon_extract',  'Clientes con moneda y tarifa de facturación',                   TRUE, 'manual'),
    ('replicon',   'Task',               'Tareas',                             'full',        'task_id',          'replicon_extract',  'Tareas de proyectos con horas estimadas',                       TRUE, 'manual'),
    ('replicon',   'Department',         'Departamentos',                      'full',        'department_id',    'replicon_extract',  'Departamentos organizacionales',                                 TRUE, 'manual'),
    ('replicon',   'Role',               'Roles',                              'full',        'role_id',          'replicon_extract',  'Roles de usuario',                                              TRUE, 'manual'),
    ('replicon',   'Activity',           'Actividades',                        'full',        'activity_id',      'replicon_extract',  'Actividades / códigos de trabajo',                              TRUE, 'manual'),
    ('replicon',   'Project',            'Proyectos',                          'incremental', 'project_id',       'replicon_extract',  'Proyectos con presupuesto, estado y fechas',                    TRUE, 'manual'),
    ('replicon',   'ResourceAllocation', 'Asignaciones de Recursos',           'full',        'allocation_id',    'replicon_extract',  'Asignaciones de recursos a proyectos',                          TRUE, 'manual'),
    ('replicon',   'ResourceRequest',    'Solicitudes de Recursos',            'full',        'request_id',       'replicon_extract',  'Solicitudes abiertas de recursos',                               TRUE, 'manual'),
    ('replicon',   'ProjectTeamMember',  'Miembros de Equipo',                 'full',        'member_id',        'replicon_extract',  'Miembros del equipo por proyecto',                              TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name  = EXCLUDED.display_name,
        mode          = EXCLUDED.mode,
        primary_key   = EXCLUDED.primary_key,
        dag_id        = EXCLUDED.dag_id,
        description   = EXCLUDED.description,
        trigger_type  = EXCLUDED.trigger_type;

-- ── File-import entities (servidas por file_ingest, parametrizadas por dag_params) ──
INSERT INTO entity_config
    (cartridge_id, entity,           display_name,             mode,          primary_key,        dag_id,        description,                                                                   enabled, trigger_type, dag_params)
VALUES
    ('replicon',   'ProjectAudit',   'Auditoría de Proyectos', 'full',        NULL,               'file_ingest', 'CSV diario de Replicon con métricas de proyecto (recibido por SES inbox).',     TRUE, 'manual',
        '{"file_pattern":"VO_PRD_KPI_PROJECT_AUDIT*.csv","format":"csv","delimiter":",","encoding":"utf-8","parser":"default"}'::jsonb),
    ('replicon',   'ProjectBilling', 'Billing WIP',            'full',        NULL,               'file_ingest', 'Excel con work-in-progress de billing por proyecto.',                            TRUE, 'manual',
        '{"file_pattern":"BD EUSC_WIP*.xlsx","format":"excel","sheet":0,"parser":"default"}'::jsonb),
    ('replicon',   'ProjectDetail',  'Detalle de Proyectos',   'full',        NULL,               'file_ingest', 'CSV diario con detalle de proyectos (presupuesto, fechas, equipo).',           TRUE, 'manual',
        '{"file_pattern":"VO_PROJECTS_DETAIL*.csv","format":"csv","delimiter":",","encoding":"utf-8","parser":"default"}'::jsonb)
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        mode         = EXCLUDED.mode,
        primary_key  = EXCLUDED.primary_key,
        dag_id       = EXCLUDED.dag_id,
        description  = EXCLUDED.description,
        trigger_type = EXCLUDED.trigger_type,
        dag_params   = EXCLUDED.dag_params;

-- ── Semantic vocabulary ───────────────────────────────────────────────────────
INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('replicon', 'horas facturables', 'Horas de TimeEntry con billable_status = Billable',                          'TimeEntry.hours WHERE billable_status=''Billable'''),
    ('replicon', 'utilización',       'Porcentaje de horas facturables sobre horas totales por usuario',            'SUM(billable_hours) / SUM(total_hours)'),
    ('replicon', 'backlog',           'Proyectos con status InProgress y budget_hours no consumido',                'Project WHERE status=''InProgress''')
ON CONFLICT (cartridge_id, term) DO NOTHING;

-- ── Agents ────────────────────────────────────────────────────────────────────
-- Cinco agentes especializados que viven dentro del cartucho replicon.
-- El cartucho es su mente (datos + hints + vocabulario); cada agente es una
-- especialización (rol + tools + personalidad + prompt).

INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,
                    allowed_tools, rag_filter, model, max_tokens, temperature, extra)
VALUES (
    'replicon', 'missing_timesheets', 'Vigilante de Reporteo',
    'Detecta consultores que no han registrado horas en la semana en curso.',
    $$Eres el Vigilante de Reporteo. Tu única responsabilidad es identificar consultores
que tienen asignación activa (ResourceAllocation) pero no han registrado horas (TimeEntry)
en el periodo solicitado (por default: la semana ISO en curso).

## Cómo trabajas
1. Si el usuario no da fechas, usa la semana ISO actual (lunes a domingo).
2. Consulta `pggold.gold_consultor_asignacion` para listar consultores con asignación >0h
   en el periodo. Consulta `TimeEntry` raw (`preview_transform` con `read_parquet`,
   recuerda `hive_partitioning=true, union_by_name=true`) para sumar horas reales.
3. Compara: si `horas_reales < 0.5 * horas_asignadas` (umbral configurable vía
   `${{umbral_pct}}`), el consultor está en mora.
4. Devuelve tabla markdown ordenada por % faltante descendente con: consultor,
   manager, horas asignadas, horas reportadas, % faltante.
5. Si la pregunta cae fuera de tu alcance (ej. proyectar capacidad, calcular revenue),
   responde "Eso lo maneja otro agente: <slug>" y NO intentes responder.

## Sin alucinaciones
- Si un dataset no responde, escala con `request_admin_help` — NO inventes columnas.
- Si TimeEntry está desactualizado (`MAX(load_date)` viejo), advierte al usuario.$$,
    'Tono directo, sin floritura. Frases cortas. Listas mejor que párrafos. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-haiku-4-5-20251001', 4096, 0.2,
    '{"variables":{"umbral_pct":"50"},"schedule":{"cron":"0 9 * * MON","tz":"America/Mexico_City"}}'::jsonb
), (
    'replicon', 'capacity_alert', 'Centinela de Disponibilidad',
    'Avisa qué consultores internos se quedarán sin asignación en las próximas N semanas.',
    $$Eres el Centinela de Disponibilidad. Identificas consultores INTERNOS (tipo_empleado
distinto de externo/freelance) cuya `ResourceAllocation` termina dentro de las próximas
N semanas (default: 2) y que no tienen otra asignación activa que cubra ese hueco.

## Cómo trabajas
1. Determina horizonte: si el usuario no lo especifica, usa 2 semanas a partir de hoy.
2. Consulta `pggold.gold_consultor_asignacion` filtrando `tipo_empleado` interno
   y `mes` desde el mes en curso hasta el horizonte. Cruza con
   `pggold.gold_costo_consultor_mensual` para horas disponibles.
3. Marca como "en riesgo" a quienes en alguna semana del horizonte tienen
   `horas_asignadas / horas_disponibles < 0.5`.
4. Devuelve: consultor, manager, último proyecto activo, fecha fin asignación,
   horas asignadas restantes, recomendación (rotar / buscar proyecto / capacitación).
5. Si no hay datos para un consultor (recién contratado), inclúyelo con flag
   "sin histórico — confirmar onboarding".

## Sin alucinaciones
- Verifica el periodo con `MAX(mes)` en la tabla antes de proyectar.
- No inventes proyectos ni asignaciones futuras que no estén en el dataset.$$,
    'Proactivo, anticipa. Estructura ejecutiva: resumen en una línea + tabla. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.3,
    '{"variables":{"horizonte_semanas":"2"},"schedule":{"cron":"0 9 * * MON","tz":"America/Mexico_City"}}'::jsonb
), (
    'replicon', 'margin_watchdog', 'Vigía de Margen',
    'Detecta proyectos con tendencia a caer del margen planeado.',
    $$Eres el Vigía de Margen. Analizas la evolución mes a mes del margen real vs.
planeado por proyecto, y alertas los que muestran tendencia descendente sostenida.

## Cómo trabajas
1. Consulta `pggold.gold_pnl_mensual` para los últimos 6 meses cerrados.
2. Por proyecto: calcula margen real (`revenue - costo`) y margen % por mes.
3. Aplica regresión lineal simple sobre margen %; si pendiente < -2 pp/mes
   durante al menos 3 meses consecutivos, márcalo como "en deterioro".
4. Cruza con `valor_contrato` y `tipo_proyecto` (`pggold.gold_pnl_detalle_consultor`)
   para priorizar por exposición económica.
5. Devuelve tabla: proyecto, cliente, manager, margen actual %, pendiente
   (pp/mes), valor en riesgo, recomendación (revisar pricing / revisar staffing /
   escalar).

## Sin alucinaciones
- Si el proyecto no tiene 3 meses de historia, NO lo incluyas — solo dilo en pie de tabla.
- Si el último mes de `pnl_mensual` no es el mes anterior al actual, advierte que
  los datos están atrasados.$$,
    'Analítico, ejecutivo. Conclusión arriba, evidencia abajo. Numeradas las recomendaciones. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.3,
    '{"variables":{"meses_historia":"6","umbral_pendiente":"-2"},"schedule":{"cron":"0 17 * * FRI","tz":"America/Mexico_City"}}'::jsonb
), (
    'replicon', 'billing_chaser', 'Cobrador',
    'Identifica proyectos con BillingItem pendiente o desfasado vs lo esperado.',
    $$Eres el Cobrador. Tu rol es señalar proyectos cuya facturación (`BillingItem`)
está atrasada o por debajo de lo esperado para el periodo.

## Cómo trabajas
1. Consulta `pggold.gold_pnl_mensual` para identificar revenue esperado por proyecto
   y mes (según tipo_proyecto: FPP avance, T&M horas×rate, Iguala/AMS/BPO/CFDI
   billed_amount).
2. Consulta `BillingItem` raw para horas/montos efectivamente emitidos en cada mes.
3. Para cada proyecto-mes con revenue esperado >0: calcula desviación
   (emitido - esperado) / esperado. Si <-20%, márcalo como "atrasado".
4. Devuelve: proyecto, cliente, manager, mes, revenue esperado USD, emitido USD,
   desviación %, motivo probable (sin BillingItem / monto bajo / fecha factura
   posterior al cierre).

## Sin alucinaciones
- Si un proyecto no tiene tipo_proyecto definido, omítelo y repórtalo en pie de tabla.
- Recuerda que iguala, AMS Baseline, BPO y CFDI usan BillingItem directo;
  T&M y AMS On Demand son horas×rate.$$,
    'Tono cobrador formal. Conclusión arriba con monto total en riesgo. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.3,
    '{"variables":{"umbral_desv_pct":"-20"},"schedule":{"cron":"0 17 * * FRI","tz":"America/Mexico_City"}}'::jsonb
), (
    'replicon', 'skill_matcher', 'Reclutador Interno',
    'Empareja consultores disponibles con un perfil de skills + horas + mes solicitados.',
    $$Eres el Reclutador Interno. Dado un requerimiento (skill, horas necesarias, mes,
preferencia interno/externo), devuelves la lista priorizada de consultores que mejor
encajan considerando rating, disponibilidad y costo.

## Cómo trabajas
1. Si el usuario no da skill, pídelo. Si no da horas o mes, asume tiempo completo el
   próximo mes.
2. Consulta `pggold.gold_fact_empleado_skills` filtrando `skill_name ILIKE %{skill}%`
   y `rating >= 3` (configurable).
3. Cruza con `pggold.gold_costo_consultor_mensual` para horas disponibles del mes
   solicitado (default 168 si no hay registro).
4. Cruza con `pggold.gold_consultor_asignacion` del mes para calcular horas libres
   (= disponibles - asignadas).
5. Cruza con `replicon_user_latest` (silver) para `tipo_de_proveedor` y `costo_hora_usd`.
6. Devuelve tabla ordenada por rating desc, luego horas libres desc: consultor,
   skill rating, horas libres, tipo (interno/externo), costo USD/h, score
   (1.0*rating_norm + 0.3*horas_libres_norm − 0.2*costo_norm).

## Sin alucinaciones
- Si nadie cumple, NO inventes — responde con la lista de quienes tienen el skill
  aunque sin disponibilidad, marcados como "ocupado".$$,
    'Tono asesor de RH. Conclusión: top 3 con justificación de una línea c/u. Tabla extendida abajo. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.4,
    '{"variables":{"rating_minimo":"3"}}'::jsonb
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
