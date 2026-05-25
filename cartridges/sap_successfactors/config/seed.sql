-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: SAP SuccessFactors HXM — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_successfactors',
    'SAP SuccessFactors HXM',
    '1.0.0',
    'SAP SuccessFactors Human Experience Management — extrae datos de Empleados, Posiciones, Departamentos, y Módulos de Talento (Candidatos, Objetivos, Desempeño).',
    'dag-based',
    'cartridge',
    'raw/sap_successfactors/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_successfactors', 'sap_successfactors_extract',     'sap_successfactors_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_successfactors', 'sap_successfactors_extract_all', 'sap_successfactors_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
-- Canonical entities, aligned with infra/init/79_sap_successfactors_alignment.sql
-- and app/config/entities.yaml. Foundation Objects use their FO* names (FODepartment,
-- FODivision, FOLocation, FOCostCenter); talent entities carry odata_entity where the
-- business name differs from the OData entityset (GoalPlan -> Goal, PerformanceReview
-- -> FormHeader, LearningItem -> Item, EmpJob_History -> EmpJobRelationships). Metadata
-- (odata_entity / mode / watermark / page_size) is inherited from entities.yaml.
INSERT INTO entity_config
    (cartridge_id, entity, odata_entity, display_name, description, mode,
     watermark_field, page_size, primary_key, dag_id, enabled, trigger_type)
VALUES
    ('sap_successfactors', 'User', 'User', 'Usuarios', 'Datos maestros del usuario (User)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpEmployment', 'EmpEmployment', 'Empleo', 'Datos de empleo (EmpEmployment)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob', 'EmpJob', 'Puesto (Job)', 'Datos de puesto (EmpJob)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpCompensation', 'EmpCompensation', 'Compensación', 'Datos de compensación (EmpCompensation)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Position', 'Position', 'Posición', 'Datos de posición (Position)', 'incremental', 'lastModifiedDateTime', 500, 'positionCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODepartment', 'FODepartment', 'Departamento', 'Objeto de fundación: departamentos', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODivision', 'FODivision', 'División', 'Objeto de fundación: divisiones', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOLocation', 'FOLocation', 'Ubicación', 'Objeto de fundación: ubicaciones', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOCostCenter', 'FOCostCenter', 'Centro de Costos', 'Objeto de fundación: centros de costo', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob_History', 'EmpJobRelationships', 'Relaciones Laborales', 'Relaciones de puesto (EmpJobRelationships)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'JobRequisition', 'JobRequisition', 'Requisición de Puesto', 'Requisiciones de empleo (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'jobReqId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Candidate', 'Candidate', 'Candidatos', 'Candidatos en pipeline (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'candidateId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningItem', 'Item', 'Items de Aprendizaje', 'Items de aprendizaje (LMS, entityset Item)', 'incremental', 'lastModifiedDateTime', 200, 'learningItemId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerformanceReview', 'FormHeader', 'Evaluación de Desempeño', 'Encabezados de formularios de evaluación (PMGM, entityset FormHeader)', 'incremental', 'lastModifiedDateTime', 200, 'formDataId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'GoalPlan', 'Goal', 'Plan de Objetivos', 'Objetivos de desempeño (entityset Goal)', 'incremental', 'lastModifiedDateTime', 200, 'planId', 'sap_successfactors_extract', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET odata_entity    = EXCLUDED.odata_entity,
        display_name    = EXCLUDED.display_name,
        description     = EXCLUDED.description,
        mode            = EXCLUDED.mode,
        watermark_field = EXCLUDED.watermark_field,
        page_size       = EXCLUDED.page_size,
        primary_key     = EXCLUDED.primary_key,
        dag_id          = EXCLUDED.dag_id,
        enabled         = EXCLUDED.enabled,
        trigger_type    = EXCLUDED.trigger_type;

-- ── Semantic vocabulary ───────────────────────────────────────────────────────
INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_successfactors', 'headcount activo', 'Número de empleados activos (startDate <= hoy <= endDate)', 'EmpEmployment WHERE isActive = true'),
    ('sap_successfactors', 'turnover', 'Rotación de personal', 'User.status changes'),
    ('sap_successfactors', 'evaluación', 'Rating en PerformanceReview', 'PerformanceReview.overallRating')
ON CONFLICT (cartridge_id, term) DO NOTHING;

-- ── Specialized agents ────────────────────────────────────────────────────────
-- Mirrored in infra/init/88_sap_successfactors_agents_seed.sql for fresh DB installs.
-- The agents table has no workspace_id (cartridge-scoped). The platform has no
-- trigger-based routing yet; "triggers" phrases live in extra as intent metadata.
INSERT INTO agents (cartridge_id, slug, name, description, instructions, personality,
                    allowed_tools, rag_filter, model, max_tokens, temperature, extra)
VALUES
(
    'sap_successfactors', 'sap_successfactors_hr_strategist', 'HR Strategist',
    'Analisis estrategico de plantilla y composicion organizacional: headcount por departamento, ubicacion y compania, y rotacion.',
    $$Eres el HR Strategist. Apoyas al HR Director con analisis estrategico de plantilla:
headcount, composicion organizacional y distribucion por departamento, ubicacion y compania.

## Como trabajas
1. Para headcount usa los golds `pggold.gold_sap_successfactors_headcount_by_department`,
   `pggold.gold_sap_successfactors_headcount_by_location` y
   `pggold.gold_sap_successfactors_headcount_by_company`. Para rotacion usa
   `pggold.gold_sap_successfactors_turnover_by_period`.
2. Los KBs `kb_sap_successfactors_headcount_by_department`,
   `kb_sap_successfactors_headcount_by_location`, `kb_sap_successfactors_headcount_by_company`
   y `kb_sap_successfactors_turnover_recent` ya devuelven estos agregados.
3. Entrega un insight con numeros concretos (totales, top N, distribucion) y, cuando ayude,
   sugiere abrir el app `sap_successfactors_workforce_overview`.
4. Si la pregunta es de talento (reclutamiento, anomalias, span), responde que lo cubre el
   Talent Advisor y no improvises.

## Sin alucinaciones
- `User.userId` esta shadowed; las familias Emp* y PerPersonal unen por claves planas.
- La compensacion (payCompValue) esta cifrada: no calcules distribucion salarial.
- Si un dataset no responde, escala con `request_admin_help`; no inventes columnas.$$,
    'Estrategico, narrativo y data-driven. Conclusion arriba con numeros, luego detalle. Sugiere el dashboard cuando ayude. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"cartridges":["sap_successfactors"],"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 2000, 0.3,
    '{"variables":{},"triggers":["headcount","plantilla","composicion","distribucion","departamento","ubicacion","compania","workforce"]}'::jsonb
),
(
    'sap_successfactors', 'sap_successfactors_talent_advisor', 'Talent Advisor',
    'Analisis de talento, reclutamiento, rotacion y salud operativa: pipeline de candidatos, calidad de datos y span of control.',
    $$Eres el Talent Advisor. Apoyas al Head of Talent y a People Analytics con analisis de
talento: pipeline de reclutamiento, rotacion, calidad de datos y span of control.

## Como trabajas
1. Para reclutamiento usa `pggold.gold_sap_successfactors_recruitment_funnel` (parcial).
   Para rotacion usa `pggold.gold_sap_successfactors_turnover_by_period`. Para calidad de
   datos usa `pggold.gold_sap_successfactors_employees_anomalies`. Para estructura de mando
   usa `pggold.gold_sap_successfactors_manager_hierarchy` (managerId real, direct_reports
   poblado).
2. Los KBs `kb_sap_successfactors_recruitment_funnel`, `kb_sap_successfactors_turnover_recent`,
   `kb_sap_successfactors_employees_anomalies` y `kb_sap_successfactors_manager_hierarchy_depth`
   ya devuelven estos agregados.
3. Entrega hallazgos accionables y, cuando ayude, sugiere abrir el app
   `sap_successfactors_talent_health`.
4. Se honesto con las limitaciones: el embudo de reclutamiento es parcial (JobApplication no
   extraida, hoy a nivel de requisicion); puede no haber rotacion hasta activar la extraccion
   de terminaciones.

## Sin alucinaciones
- `User.userId` esta shadowed; trabaja con los agregados del gold.
- Si un dataset no responde, escala con `request_admin_help`; no inventes columnas.$$,
    'Analitico, orientado a accion y transparente. Hallazgos priorizados, honesto sobre datos parciales. Idioma del usuario.',
    '["refinement__query_dataset","refinement__preview_transform","refinement__get_schema","refinement__describe_silver","mcp-infra__search_rag","mcp-infra__request_admin_help"]'::jsonb,
    '{"cartridges":["sap_successfactors"],"kinds":["document","schema"]}'::jsonb,
    'claude-sonnet-4-6', 2000, 0.3,
    '{"variables":{},"triggers":["rotacion","turnover","reclutamiento","candidatos","requisiciones","anomalias","manager","span","talento"]}'::jsonb
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
