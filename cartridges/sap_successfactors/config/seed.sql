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
-- display_name: nombre legible para UI y reportes
-- mode:         full | incremental
-- dag_id:       DAG que maneja la extracción
-- trigger_type: manual | scheduled
INSERT INTO entity_config
    (cartridge_id, entity,               display_name,                         mode,          primary_key,        dag_id,              description,                                                   enabled, trigger_type)
VALUES
    ('sap_successfactors', 'User',               'Usuarios',                 'incremental', 'userId',           'sap_successfactors_extract', 'Datos maestros del usuario (User)', TRUE, 'manual'),
    ('sap_successfactors', 'EmpEmployment',      'Empleo',                   'incremental', 'userId',           'sap_successfactors_extract', 'Datos de Empleo (EmpEmployment)', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob',             'Puesto (Job)',             'incremental', 'userId',           'sap_successfactors_extract', 'Datos de Puesto (EmpJob)', TRUE, 'manual'),
    ('sap_successfactors', 'EmpCompensation',    'Compensación',             'incremental', 'userId',           'sap_successfactors_extract', 'Datos de Compensación (EmpCompensation)', TRUE, 'manual'),
    ('sap_successfactors', 'Position',           'Posición',                 'full',        'positionCode',     'sap_successfactors_extract', 'Datos de Posición', TRUE, 'manual'),
    ('sap_successfactors', 'Department',         'Departamento',             'full',        'externalCode',     'sap_successfactors_extract', 'Datos de Departamento', TRUE, 'manual'),
    ('sap_successfactors', 'Division',           'División',                 'full',        'externalCode',     'sap_successfactors_extract', 'Datos de División', TRUE, 'manual'),
    ('sap_successfactors', 'Location',           'Ubicación',                'full',        'externalCode',     'sap_successfactors_extract', 'Datos de Ubicación', TRUE, 'manual'),
    ('sap_successfactors', 'CostCenter',         'Centro de Costos',         'full',        'externalCode',     'sap_successfactors_extract', 'Datos de Centro de Costos', TRUE, 'manual'),
    ('sap_successfactors', 'EmpJob_History',     'Relaciones Laborales',     'incremental', 'userId',           'sap_successfactors_extract', 'Histórico Job (EmpJobRelationships)', TRUE, 'manual'),
    ('sap_successfactors', 'JobRequisition',     'Requisición de Puesto',    'incremental', 'jobReqId',         'sap_successfactors_extract', 'Requisiciones de empleo activas', TRUE, 'manual'),
    ('sap_successfactors', 'Candidate',          'Candidatos',               'incremental', 'candidateId',      'sap_successfactors_extract', 'Candidatos en pipeline', TRUE, 'manual'),
    ('sap_successfactors', 'LearningItem',       'Items de Aprendizaje',     'incremental', 'learningItemId',   'sap_successfactors_extract', 'Cursos de SF LMS', TRUE, 'manual'),
    ('sap_successfactors', 'PerformanceReview',  'Evaluación de Desempeño',  'incremental', 'formDataId',       'sap_successfactors_extract', 'Evaluaciones de Desempeño (PMGM)', TRUE, 'manual'),
    ('sap_successfactors', 'GoalPlan',           'Plan de Objetivos',        'incremental', 'planId',           'sap_successfactors_extract', 'Plan de Objetivos', TRUE, 'manual')
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
    ('sap_successfactors', 'headcount activo', 'Número de empleados activos (startDate <= hoy <= endDate)', 'EmpEmployment WHERE isActive = true'),
    ('sap_successfactors', 'turnover', 'Rotación de personal', 'User.status changes'),
    ('sap_successfactors', 'evaluación', 'Rating en PerformanceReview', 'PerformanceReview.overallRating')
ON CONFLICT (cartridge_id, term) DO NOTHING;
