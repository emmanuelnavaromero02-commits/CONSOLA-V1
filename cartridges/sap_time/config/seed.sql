-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: Replicon PSA — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── Cartridge header ──────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_time',
    'Replicon PSA',
    '3.0.0',
    'Replicon Professional Services Automation — extrae datos de workforce: usuarios, proyectos, tiempo registrado, tareas, clientes, facturas, asignaciones y gastos.',
    'dag-based',
    'cartridge',
    'raw/sap_time/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        updated_at  = NOW();

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_time', 'sap_time_extract',     'sap_time_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_time', 'sap_time_extract_all', 'sap_time_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
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
    ('sap_time',   'TimeEntry',          'Registros de Tiempo',                'incremental', 'entry_id',         'sap_time_extract',  'Registros de tiempo con horas, estado facturable y aprobación', TRUE, 'manual'),
    ('sap_time',   'Timesheet',          'Hojas de Tiempo',                    'incremental', 'timesheet_id',     'sap_time_extract',  'Hojas de tiempo con período, usuario y aprobación',             TRUE, 'manual'),
    ('sap_time',   'ExpenseEntry',       'Gastos',                             'incremental', 'expense_id',       'sap_time_extract',  'Gastos con monto, categoría y flag facturable',                 TRUE, 'manual'),
    ('sap_time',   'BillingItem',        'Items de Facturación',               'incremental', 'billing_item_id',  'sap_time_extract',  'Items de facturación por proyecto',                             TRUE, 'manual'),
    ('sap_time',   'InvoiceItem',        'Items de Factura',                   'incremental', 'invoice_item_id',  'sap_time_extract',  'Items de factura con monto, horas y tarifa',                    TRUE, 'manual'),
    ('sap_time',   'CostItem',           'Items de Costo',                     'incremental', 'cost_item_id',     'sap_time_extract',  'Items de costo por proyecto',                                   TRUE, 'manual'),
    ('sap_time',   'ProfitItem',         'Items de Ganancia',                  'incremental', 'profit_item_id',   'sap_time_extract',  'Items de ganancia por proyecto',                                TRUE, 'manual'),
    ('sap_time',   'User',               'Usuarios',                           'full',        'user_id',          'sap_time_extract',  'Usuarios del sistema con costos y tarifas',                     TRUE, 'manual'),
    ('sap_time',   'Client',             'Clientes',                           'full',        'client_id',        'sap_time_extract',  'Clientes con moneda y tarifa de facturación',                   TRUE, 'manual'),
    ('sap_time',   'Task',               'Tareas',                             'full',        'task_id',          'sap_time_extract',  'Tareas de proyectos con horas estimadas',                       TRUE, 'manual'),
    ('sap_time',   'Department',         'Departamentos',                      'full',        'department_id',    'sap_time_extract',  'Departamentos organizacionales',                                 TRUE, 'manual'),
    ('sap_time',   'Role',               'Roles',                              'full',        'role_id',          'sap_time_extract',  'Roles de usuario',                                              TRUE, 'manual'),
    ('sap_time',   'Activity',           'Actividades',                        'full',        'activity_id',      'sap_time_extract',  'Actividades / códigos de trabajo',                              TRUE, 'manual'),
    ('sap_time',   'Project',            'Proyectos',                          'incremental', 'project_id',       'sap_time_extract',  'Proyectos con presupuesto, estado y fechas',                    TRUE, 'manual'),
    ('sap_time',   'ResourceAssignment', 'Asignaciones de Recursos',           'incremental', 'assignment_id',    'sap_time_extract',  'Asignaciones de recursos a proyectos',                          TRUE, 'manual'),
    ('sap_time',   'ProjectTeamMember',  'Miembros de Equipo',                 'full',        'member_id',        'sap_time_extract',  'Miembros del equipo por proyecto',                              TRUE, 'manual')
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
    ('sap_time', 'horas facturables', 'Horas de TimeEntry con billable_status = Billable',                          'TimeEntry.hours WHERE billable_status=''Billable'''),
    ('sap_time', 'utilización',       'Porcentaje de horas facturables sobre horas totales por usuario',            'SUM(billable_hours) / SUM(total_hours)'),
    ('sap_time', 'backlog',           'Proyectos con status InProgress y budget_hours no consumido',                'Project WHERE status=''InProgress''')
ON CONFLICT (cartridge_id, term) DO NOTHING;
