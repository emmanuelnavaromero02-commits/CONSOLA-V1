-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: SAP S/4HANA Core — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_s4hana',
    'SAP S/4HANA Core',
    '1.0.0',
    'SAP S/4HANA on-premise / S4HANA — extrae datos maestros de empleados, estructura organizacional, ausencias y horarios.',
    'dag-based',
    'cartridge',
    'raw/sap_s4hana/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_s4hana', 'sap_s4hana_extract',     'sap_s4hana_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_s4hana', 'sap_s4hana_extract_all', 'sap_s4hana_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
-- display_name: nombre legible para UI y reportes
-- mode:         full | incremental
-- dag_id:       DAG que maneja la extracción
-- trigger_type: manual | scheduled
INSERT INTO entity_config
    (cartridge_id, entity,               display_name,                         mode,          primary_key,        dag_id,              description,                                                   enabled, trigger_type)
VALUES
    ('sap_s4hana', 'EmployeeMaster',      'Maestro de Empleados',       'incremental', 'Pernr',        'sap_s4hana_extract', 'Asignación organizacional del empleado (infotipo 0001)', TRUE, 'manual'),
    ('sap_s4hana', 'PersonalData',        'Datos Personales',           'incremental', 'Pernr',        'sap_s4hana_extract', 'Datos personales del empleado', TRUE, 'manual'),
    ('sap_s4hana', 'ContractData',        'Datos de Contrato',          'incremental', 'Pernr',        'sap_s4hana_extract', 'Datos del contrato del empleado', TRUE, 'manual'),
    ('sap_s4hana', 'OrgUnit',             'Unidad Organizacional',      'full',        'ObjId',        'sap_s4hana_extract', 'Unidad Organizacional', TRUE, 'manual'),
    ('sap_s4hana', 'Position',            'Posición',                   'full',        'ObjId',        'sap_s4hana_extract', 'Posición', TRUE, 'manual'),
    ('sap_s4hana', 'CostCenter',          'Centro de Costos',           'full',        'Kostl',        'sap_s4hana_extract', 'Centro de Costos', TRUE, 'manual'),
    ('sap_s4hana', 'JobCode',             'Código de Trabajo',          'full',        'ObjId',        'sap_s4hana_extract', 'Código de Trabajo', TRUE, 'manual'),
    ('sap_s4hana', 'EmployeeActions',     'Acciones de Empleados',      'incremental', 'Pernr',        'sap_s4hana_extract', 'Acciones de personal', TRUE, 'manual'),
    ('sap_s4hana', 'LeaveAbsence',        'Ausencias',                  'incremental', 'Pernr',        'sap_s4hana_extract', 'Ausencias y permisos', TRUE, 'manual'),
    ('sap_s4hana', 'WorkSchedule',        'Horarios',                   'full',        'Pernr',        'sap_s4hana_extract', 'Horario de trabajo', TRUE, 'manual')
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
    ('sap_s4hana', 'headcount activo', 'Número de empleados activos hoy', 'EmployeeMaster WHERE Endda >= CURRENT_DATE AND Begda <= CURRENT_DATE'),
    ('sap_s4hana', 'ausencia', 'Días de ausencia', 'LeaveAbsence.Abwtg')
ON CONFLICT (cartridge_id, term) DO NOTHING;
