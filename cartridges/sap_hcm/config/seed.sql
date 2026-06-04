-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: SAP HCM Core — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: agent inserts use target-less ON CONFLICT DO NOTHING so they
-- work with both the original global agents constraint and the later
-- workspace-aware partial indexes.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_hcm',
    'SAP HCM Core',
    '1.0.0',
    'SAP HCM on-premise / S4HANA — extrae datos maestros de empleados, estructura organizacional, ausencias y horarios.',
    'dag-based',
    'cartridge',
    'raw/sap_hcm/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_hcm', 'sap_hcm_extract',     'sap_hcm_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_hcm', 'sap_hcm_extract_all', 'sap_hcm_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
-- display_name: nombre legible para UI y reportes
-- mode:         full | incremental
-- dag_id:       DAG que maneja la extracción
-- trigger_type: manual | scheduled
INSERT INTO entity_config
    (cartridge_id, entity,               display_name,                         mode,          primary_key,        dag_id,              description,                                                   enabled, trigger_type)
VALUES
    ('sap_hcm', 'EmployeeMaster',      'Maestro de Empleados',       'incremental', 'Pernr',        'sap_hcm_extract', 'Asignación organizacional del empleado (infotipo 0001)', TRUE, 'manual'),
    ('sap_hcm', 'PersonalData',        'Datos Personales',           'incremental', 'Pernr',        'sap_hcm_extract', 'Datos personales del empleado', TRUE, 'manual'),
    ('sap_hcm', 'ContractData',        'Datos de Contrato',          'incremental', 'Pernr',        'sap_hcm_extract', 'Datos del contrato del empleado', TRUE, 'manual'),
    ('sap_hcm', 'OrgUnit',             'Unidad Organizacional',      'full',        'ObjId',        'sap_hcm_extract', 'Unidad Organizacional', TRUE, 'manual'),
    ('sap_hcm', 'Position',            'Posición',                   'full',        'ObjId',        'sap_hcm_extract', 'Posición', TRUE, 'manual'),
    ('sap_hcm', 'CostCenter',          'Centro de Costos',           'full',        'Kostl',        'sap_hcm_extract', 'Centro de Costos', TRUE, 'manual'),
    ('sap_hcm', 'JobCode',             'Código de Trabajo',          'full',        'ObjId',        'sap_hcm_extract', 'Código de Trabajo', TRUE, 'manual'),
    ('sap_hcm', 'EmployeeActions',     'Acciones de Empleados',      'incremental', 'Pernr',        'sap_hcm_extract', 'Acciones de personal', TRUE, 'manual'),
    ('sap_hcm', 'LeaveAbsence',        'Ausencias',                  'incremental', 'Pernr',        'sap_hcm_extract', 'Ausencias y permisos', TRUE, 'manual'),
    ('sap_hcm', 'WorkSchedule',        'Horarios',                   'full',        'Pernr',        'sap_hcm_extract', 'Horario de trabajo', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET display_name  = EXCLUDED.display_name,
        mode          = EXCLUDED.mode,
        primary_key   = EXCLUDED.primary_key,
        dag_id        = EXCLUDED.dag_id,
        description   = EXCLUDED.description,
        trigger_type  = EXCLUDED.trigger_type;

-- ── Semantic vocabulary ───────────────────────────────────────────────────────
INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_hcm', 'headcount activo', 'Número de empleados activos hoy', 'EmployeeMaster WHERE Endda >= CURRENT_DATE AND Begda <= CURRENT_DATE'),
    ('sap_hcm', 'ausencia', 'Días de ausencia', 'LeaveAbsence.Abwtg')
ON CONFLICT (cartridge_id, term) DO NOTHING;

-- ── Specialized agents ────────────────────────────────────────────────────────
-- Mirrored in infra/init/84_sap_hcm_agents_seed.sql for fresh DB installs. The
-- agents table has no workspace_id (cartridge-scoped). The platform has no
-- trigger-based routing yet; "triggers" phrases live in extra as intent metadata.
INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,
                    allowed_tools, rag_filter, model, max_tokens, temperature, extra)
VALUES
(
    'sap_hcm', 'sap_hcm_auditor_org_chart', 'Auditor de Organigrama',
    'Detecta problemas estructurales y de calidad de datos en la plantilla: empleados sin centro de costo, sin posicion, baja-pero-activo y huecos de jerarquia.',
    $$Eres el Auditor de Organigrama. Tu responsabilidad es detectar problemas
estructurales y de calidad de datos en la plantilla: empleados sin centro de costo,
sin posicion, con baja registrada pero aun marcados como activos, y huecos de jerarquia.

## Como trabajas
1. Parte de `pggold.gold_employees_anomalies` (un row por hallazgo: anomaly_type,
   severity, details). Agrupa por anomaly_type y severity para el panorama.
2. Para la estructura de mando consulta `pggold.gold_manager_hierarchy` (direct_reports,
   depth). Los KBs `kb_sap_hcm_employees_anomalies_active` y
   `kb_sap_hcm_manager_span_of_control` ya devuelven estos agregados.
3. Devuelve una tabla markdown de hallazgos ordenada por severity (high primero) con:
   tipo de anomalia, severidad y numero de casos.
4. Cierra con UNA recomendacion accionable y priorizada (que corregir primero y por que).
5. Si la pregunta cae fuera de tu alcance (por ejemplo costo o nomina), responde que lo
   cubre otro agente y no improvises.

## Sin alucinaciones
- Si un dataset no responde, escala con `request_admin_help`; no inventes columnas.
- `Pernr` esta shadowed: reporta conteos agregados, nunca el identificador de un
  empleado individual.
- Recuerda que `gold_manager_hierarchy` hoy puede venir plana (sin fuente HRP1001); si
  direct_reports es 0 para todos, dilo de forma explicita.$$,
    'Ejecutivo y directo. Conclusion arriba, hallazgos en tabla, una recomendacion accionable. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.2,
    '{"variables":{},"triggers":["que problemas tengo en mi plantilla","auditoria de personal","empleados sin centro de costo","anomalias de empleados","calidad de datos de empleados"]}'::jsonb
),
(
    'sap_hcm', 'sap_hcm_analista_workforce', 'Analista de Plantilla',
    'Analiza la composicion y la dinamica de la plantilla: headcount por departamento, centro de costo y tipo de posicion, y evolucion de ausencias.',
    $$Eres el Analista de Plantilla. Analizas la composicion y la dinamica de la fuerza
laboral: cuantos empleados hay y como se distribuyen, y como evolucionan las ausencias.

## Como trabajas
1. Para composicion usa `pggold.gold_headcount_by_department`,
   `pggold.gold_headcount_by_costcenter` y `pggold.gold_headcount_by_position_type`
   (toma el ultimo snapshot_month).
2. Para la dinamica de ausencias usa `pggold.gold_absence_by_type_and_month` (dias
   habiles y empleados afectados por mes y tipo).
3. Los KBs `kb_sap_hcm_headcount_active_by_department`, `kb_sap_hcm_headcount_by_costcenter`,
   `kb_sap_hcm_workforce_composition_by_position_type` y `kb_sap_hcm_absence_trend_monthly`
   ya devuelven estos agregados.
4. Entrega un insight narrativo con numeros concretos (totales, top departamentos,
   tendencia) y, cuando ayude, sugiere abrir el app `sap_hcm_headcount_dashboard` o
   `sap_hcm_people_quality_dashboard` para verlo de forma grafica.
5. Distingue snapshot (headcount es la foto del ultimo mes) de serie temporal (ausencias
   mes a mes).

## Sin alucinaciones
- Confirma el periodo con el maximo `snapshot_month` o `absence_month` antes de afirmar
  tendencias.
- `Pernr` esta shadowed: trabaja con agregados.
- Si falta un gold, baja al silver y avisalo; no inventes cifras.$$,
    'Analitico y narrativo, basado en datos. Insight con numeros y, cuando ayude, sugiere el dashboard. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 8192, 0.3,
    '{"variables":{},"triggers":["como se compone mi plantilla","headcount por departamento","evolucion de ausencias","distribucion de empleados","tendencias de personal"]}'::jsonb
)
ON CONFLICT DO NOTHING;
