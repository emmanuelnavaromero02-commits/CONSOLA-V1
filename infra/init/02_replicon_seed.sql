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

-- ── API connections (metadata only — credentials viven en Airflow Connections) ──
-- Airflow UI > Admin > Connections > +:
--   Conn Id: replicon_analytics  |  Conn Type: HTTP
--   Host: https://<tenant>.replicon.com/analyticsapi  |  Password: <bearer_token>
INSERT INTO cartridge_connections (cartridge_id, conn_id, description, auth_type, poll_strategy)
VALUES
    ('replicon', 'analytics', 'API Analytics (BI) — extracts asíncronos: POST /extracts → poll → CSV',         'bearer_token', 'async_extract'),
    ('replicon', 'services',  'API Services (Transaccional) — proyectos, usuarios, timesheets en tiempo real', 'bearer_token', 'direct_get')
ON CONFLICT (cartridge_id, conn_id) DO NOTHING;

-- ── DAGs ──────────────────────────────────────────────────────────────────────
INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('replicon', 'replicon_extract',          'replicon_extract.py',          'Extrae una entidad vía Analytics API (async)',       'on-demand', '["entity","mode","from_date","to_date"]'),
    ('replicon', 'replicon_extract_all',      'replicon_extract_all.py',      'Extrae todas las entidades Analytics en secuencia',  'on-demand', '["mode","entities"]'),
    ('replicon', 'replicon_projects_detail',  'replicon_projects_detail.py',  'Extrae ProjectDetail vía Services API (GET síncrono)', 'on-demand', '["mode"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

-- ── Entities ──────────────────────────────────────────────────────────────────
INSERT INTO entity_config
    (cartridge_id, entity,               mode,          watermark_field,  primary_key,        connection_id, dag_id,              description,                                                   enabled)
VALUES
    ('replicon',   'TimeEntry',          'incremental', 'last_modified',  'entry_id',         'analytics',   'replicon_extract',  'Registros de tiempo con horas, estado facturable y aprobación', TRUE),
    ('replicon',   'Timesheet',          'incremental', 'last_modified',  'timesheet_id',     'analytics',   'replicon_extract',  'Hojas de tiempo con período, usuario y aprobación',             TRUE),
    ('replicon',   'ExpenseEntry',       'incremental', 'last_modified',  'expense_id',       'analytics',   'replicon_extract',  'Gastos con monto, categoría y flag facturable',                 TRUE),
    ('replicon',   'BillingItem',        'incremental', 'last_modified',  'billing_item_id',  'analytics',   'replicon_extract',  'Items de facturación por proyecto',                             TRUE),
    ('replicon',   'InvoiceItem',        'incremental', 'last_modified',  'invoice_item_id',  'analytics',   'replicon_extract',  'Items de factura con monto, horas y tarifa',                    TRUE),
    ('replicon',   'CostItem',           'incremental', 'last_modified',  'cost_item_id',     'analytics',   'replicon_extract',  'Items de costo por proyecto',                                   TRUE),
    ('replicon',   'ProfitItem',         'incremental', 'last_modified',  'profit_item_id',   'analytics',   'replicon_extract',  'Items de ganancia por proyecto',                                TRUE),
    -- Services API — extraídas por replicon_projects_detail (GET síncrono paginado)
    ('replicon',   'ProjectDetail',      'incremental', 'lastUpdated',    'project_id',       'services',    'replicon_projects_detail', 'Detalle completo de proyectos: budget, estado, fechas, equipo', TRUE),
    ('replicon',   'User',               'full',        'last_modified',  'user_id',          'services',    'replicon_projects_detail', 'Usuarios del sistema con costos y tarifas',                     TRUE),
    ('replicon',   'Client',             'full',        'last_modified',  'client_id',        'services',    'replicon_projects_detail', 'Clientes con moneda y tarifa de facturación',                   TRUE),
    ('replicon',   'Task',               'full',        'last_modified',  'task_id',          'services',    'replicon_projects_detail', 'Tareas de proyectos con horas estimadas',                       TRUE),
    ('replicon',   'Department',         'full',        NULL,             'department_id',    'services',    'replicon_extract',        'Departamentos organizacionales',                                 TRUE),
    ('replicon',   'Role',               'full',        NULL,             'role_id',          'services',    'replicon_projects_detail', 'Roles de usuario',                                              TRUE),
    ('replicon',   'Activity',           'full',        NULL,             'activity_id',      'services',    'replicon_projects_detail', 'Actividades / códigos de trabajo',                              TRUE),
    ('replicon',   'Project',            'incremental', 'last_modified',  'project_id',       'services',    'replicon_projects_detail', 'Proyectos con presupuesto, estado y fechas',                    TRUE),
    ('replicon',   'ResourceAssignment', 'incremental', 'last_modified',  'assignment_id',    'services',    'replicon_projects_detail', 'Asignaciones de recursos a proyectos',                          TRUE),
    ('replicon',   'ProjectTeamMember',  'full',        NULL,             'member_id',        'services',    'replicon_projects_detail', 'Miembros del equipo por proyecto',                              TRUE)
ON CONFLICT (cartridge_id, entity) DO UPDATE
    SET mode           = EXCLUDED.mode,
        watermark_field= EXCLUDED.watermark_field,
        primary_key    = EXCLUDED.primary_key,
        connection_id  = EXCLUDED.connection_id,
        dag_id         = EXCLUDED.dag_id,
        description    = EXCLUDED.description;

-- ── Semantic vocabulary ───────────────────────────────────────────────────────
INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('replicon', 'horas facturables', 'Horas de TimeEntry con billable_status = Billable',                          'TimeEntry.hours WHERE billable_status=''Billable'''),
    ('replicon', 'utilización',       'Porcentaje de horas facturables sobre horas totales por usuario',            'SUM(billable_hours) / SUM(total_hours)'),
    ('replicon', 'backlog',           'Proyectos con status InProgress y budget_hours no consumido',                'Project WHERE status=''InProgress''')
ON CONFLICT (cartridge_id, term) DO NOTHING;
