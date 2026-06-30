-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: SAP SuccessFactors HXM — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: agent inserts use target-less ON CONFLICT DO NOTHING so they
-- work with both the original global agents constraint and the later
-- workspace-aware partial indexes.
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
    ('sap_successfactors', 'Position', 'Position', 'Posición', 'Datos de posición (Position)', 'incremental', 'lastModifiedDateTime', 500, 'code', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODepartment', 'FODepartment', 'Departamento', 'Objeto de fundación: departamentos', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FODivision', 'FODivision', 'División', 'Objeto de fundación: divisiones', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOLocation', 'FOLocation', 'Ubicación', 'Objeto de fundación: ubicaciones', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOCostCenter', 'FOCostCenter', 'Centro de Costos', 'Objeto de fundación: centros de costo', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob_History', 'EmpJobRelationships', 'Relaciones Laborales', 'Relaciones de puesto (EmpJobRelationships)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'JobRequisition', 'JobRequisition', 'Requisición de Puesto', 'Requisiciones de empleo (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'jobReqId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Candidate', 'Candidate', 'Candidatos', 'Candidatos en pipeline (Recruiting)', 'incremental', 'lastModifiedDateTime', 200, 'candidateId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningItem', 'Item', 'Items de Aprendizaje', 'Items de aprendizaje (LMS, entityset Item)', 'incremental', 'lastModifiedDateTime', 200, 'learningItemId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerformanceReview', 'FormHeader', 'Evaluación de Desempeño', 'Encabezados de formularios de evaluación (PMGM, entityset FormHeader)', 'incremental', 'lastModifiedDateTime', 200, 'formDataId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'GoalPlan', 'Goal', 'Plan de Objetivos', 'Objetivos de desempeño (entityset Goal)', 'incremental', 'lastModifiedDateTime', 200, 'id', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FormPerfPotSummarySection', 'FormPerfPotSummarySection', 'Resumen Performance/Potential', 'Resumen de desempeno y potencial para 9-box', 'incremental', 'lastModifiedDateTime', 200, 'formDataId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FormObjective', 'FormObjective', 'Objetivos de Formulario', 'Objetivos dentro de formularios PMGM', 'incremental', 'lastModifiedDateTime', 200, 'objectiveId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FormObjectiveDetails', 'FormObjectiveDetails', 'Detalle de Objetivos', 'Detalle de objetivos PMGM', 'incremental', 'lastModifiedDateTime', 200, 'objectiveDetailId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SimpleGoal', 'SimpleGoal', 'Objetivo Simple', 'Entidad alternativa de objetivos SimpleGoal', 'incremental', 'lastModifiedDateTime', 200, 'id', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'GoalAchievements', 'GoalAchievements', 'Logros de Objetivos', 'Avances y logros de objetivos', 'incremental', 'lastModifiedDateTime', 200, 'achievementId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CalibrationSession', 'CalibrationSession', 'Sesion de Calibracion', 'Sesiones de calibracion de talento/desempeno', 'incremental', 'lastModifiedDateTime', 200, 'sessionId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CalibrationSessionSubject', 'CalibrationSessionSubject', 'Sujetos de Calibracion', 'Empleados incluidos en sesiones de calibracion', 'incremental', 'lastModifiedDateTime', 500, 'subjectId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CalibrationSubjectRank', 'CalibrationSubjectRank', 'Ranking de Calibracion', 'Ranking/calibracion por sujeto', 'incremental', 'lastModifiedDateTime', 500, 'rankId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerPerson', 'PerPerson', 'Persona', 'Informacion personal (PerPerson)', 'incremental', 'lastModifiedDateTime', 200, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerPersonal', 'PerPersonal', 'Datos Personales', 'Datos personales efectivo-fechados (PerPersonal)', 'incremental', 'lastModifiedDateTime', 200, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerEmail', 'PerEmail', 'Correos', 'Correos de la persona (PerEmail)', 'incremental', 'lastModifiedDateTime', 500, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerPhone', 'PerPhone', 'Telefonos', 'Telefonos de la persona (PerPhone)', 'incremental', 'lastModifiedDateTime', 500, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerAddressDEFLT', 'PerAddressDEFLT', 'Direcciones', 'Direcciones de la persona (PerAddressDEFLT)', 'incremental', 'lastModifiedDateTime', 500, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PerNationalId', 'PerNationalId', 'ID Nacional', 'Identificaciones nacionales (PerNationalId)', 'full', NULL, 200, 'personIdExternal', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpPayCompRecurring', 'EmpPayCompRecurring', 'Pago Recurrente', 'Componentes de pago recurrentes (EmpPayCompRecurring)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpPayCompNonRecurring', 'EmpPayCompNonRecurring', 'Pago No Recurrente', 'Componentes de pago no recurrentes (EmpPayCompNonRecurring)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'PaymentInformationDetailV3', 'PaymentInformationDetailV3', 'Detalle de Pago V3', 'Detalles bancarios / metodo de pago (PaymentInformationDetailV3)', 'incremental', 'lastModifiedDateTime', 200, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmpEmploymentTermination', 'EmpEmploymentTermination', 'Baja de Empleo', 'Terminaciones de empleo (EmpEmploymentTermination)', 'incremental', 'lastModifiedDateTime', 200, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOCompany', 'FOCompany', 'Compania', 'Objeto de fundacion: companias', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOBusinessUnit', 'FOBusinessUnit', 'Unidad de Negocio', 'Objeto de fundacion: unidades de negocio', 'full', NULL, 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOJobCode', 'FOJobCode', 'Codigo de Puesto', 'Objeto de fundacion: codigos de puesto', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOPayGrade', 'FOPayGrade', 'Grado de Pago', 'Objeto de fundacion: grados de pago no salariales', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'EmployeeTime', 'EmployeeTime', 'Tiempo del Empleado', 'Registros de tiempo del empleado (EmployeeTime)', 'incremental', 'lastModifiedDateTime', 500, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'TimeAccount', 'TimeAccount', 'Cuenta de Tiempo', 'Cuentas de tiempo (TimeAccount)', 'incremental', 'lastModifiedDateTime', 500, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'WorkSchedule', 'WorkSchedule', 'Horario de Trabajo', 'Horarios de trabajo (WorkSchedule)', 'incremental', 'lastModifiedDateTime', 500, 'userId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FOEventReason', 'FOEventReason', 'Razon de Evento', 'Foundation Object: razones de evento para movimientos y bajas', 'full', NULL, 1000, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'JobApplication', 'JobApplication', 'Aplicacion Recruiting', 'Aplicaciones que unen candidato, requisicion y etapa', 'incremental', 'lastModifiedDateTime', 200, 'applicationId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CompetencyEntity', 'CompetencyEntity', 'Competencia', 'Catalogo tenant de competencias', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'UserSkill', 'UserSkill', 'Skill de Usuario', 'Skills/proficiencies por empleado', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SkillProfile', 'SkillProfile', 'Perfil de Skill', 'Entidad alternativa de skills/proficiencies por empleado', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'WorkerCompetencyAssessment', 'WorkerCompetencyAssessment', 'Evaluacion Competencia Trabajador', 'Evaluaciones de competencias por empleado', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'FormCompetency', 'FormCompetency', 'Competencia de Formulario', 'Ratings de competencias en formularios PMGM', 'incremental', 'lastModifiedDateTime', 500, 'formCompetencyId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SysOverallCompetency', 'SysOverallCompetency', 'Competencia Overall', 'Score overall de competencias en PMGM', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SkillEntity', 'SkillEntity', 'Catalogo de Skills', 'Catalogo Talent Intelligence Hub de skills', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CareerWorksheet', 'CareerWorksheet', 'Career Worksheet', 'Enriquecimiento/custom de roles objetivo y readiness declarada', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CareerInterest', 'CareerInterest', 'Interes de Carrera', 'Enriquecimiento/custom de intereses y movilidad', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'DevGoal', 'DevGoal', 'Meta de Desarrollo', 'Metas de desarrollo y aspiracion', 'incremental', 'lastModifiedDateTime', 500, 'id', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'DevGoalCompetency', 'DevGoalCompetency', 'Competencia de Meta', 'Competencias asociadas a metas de desarrollo', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'TalentPool', 'TalentPool', 'Talent Pool', 'Catalogo de talent pools', 'incremental', 'lastModifiedDateTime', 500, 'poolId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'TalentPoolNav', 'TalentPoolNav', 'Miembros Talent Pool', 'Membresias de empleados en talent pools', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'SuccessionNomination', 'SuccessionNomination', 'Nominacion Sucesion', 'Nominaciones de sucesion y readiness', 'incremental', 'lastModifiedDateTime', 500, 'externalCode', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningAssignment', 'LearningAssignment', 'Asignacion Learning', 'Asignaciones LMS por empleado y item', 'incremental', 'lastModifiedDateTime', 500, 'assignmentId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningHistory', 'LearningHistory', 'Historial Learning', 'Historial LMS completado y certificaciones', 'incremental', 'lastModifiedDateTime', 500, 'historyId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'UserCourses', 'UserCourses', 'Cursos de Usuario', 'Learning v4: cursos asignados por usuario', 'incremental', 'lastModifiedDateTime', 500, 'assignmentId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'UserPrograms', 'UserPrograms', 'Programas de Usuario', 'Learning v4: programas asignados por usuario', 'incremental', 'lastModifiedDateTime', 500, 'assignmentId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'LearningEvents', 'LearningEvents', 'Eventos Learning', 'Learning v4: eventos completados', 'incremental', 'lastModifiedDateTime', 500, 'eventId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'Curricula', 'Curricula', 'Curricula', 'Learning v4: curriculas/certificaciones', 'incremental', 'lastModifiedDateTime', 500, 'curriculumId', 'sap_successfactors_extract', TRUE, 'manual'),
    ('sap_successfactors', 'CatalogsFeed', 'CatalogsFeed', 'Catalogo Learning', 'Learning v4: catalog feed', 'incremental', 'lastModifiedDateTime', 500, 'itemId', 'sap_successfactors_extract', TRUE, 'manual')
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
ON CONFLICT DO NOTHING;
