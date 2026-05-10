-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridge: Replicon PSA — seed configuration
-- Run once to register this cartridge in a new installation.
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

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
    ('replicon', 'replicon_extract',     'replicon_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)',  'on-demand', '["entity","mode","from_date","to_date"]'),
    ('replicon', 'replicon_extract_all', 'replicon_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',       'on-demand', '["mode","entities"]')
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
    ('replicon',   'ResourceAssignment', 'Asignaciones de Recursos',           'incremental', 'assignment_id',    'replicon_extract',  'Asignaciones de recursos a proyectos',                          TRUE, 'manual'),
    ('replicon',   'ProjectTeamMember',  'Miembros de Equipo',                 'full',        'member_id',        'replicon_extract',  'Miembros del equipo por proyecto',                              TRUE, 'manual')
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
    ('replicon', 'horas facturables', 'Horas de TimeEntry con billable_status = Billable',                          'TimeEntry.hours WHERE billable_status=''Billable'''),
    ('replicon', 'utilización',       'Porcentaje de horas facturables sobre horas totales por usuario',            'SUM(billable_hours) / SUM(total_hours)'),
    ('replicon', 'backlog',           'Proyectos con status InProgress y budget_hours no consumido',                'Project WHERE status=''InProgress''')
ON CONFLICT (cartridge_id, term) DO NOTHING;
