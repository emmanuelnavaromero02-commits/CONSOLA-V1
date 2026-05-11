-- ─────────────────────────────────────────────────────────────────────────────
-- MODecissions Cartridges: SAP (SuccessFactors, HCM, S/4HANA) — header seed
--
-- Registers the SAP cartridges in the `cartridges` table so they appear in
-- the Studio "Fuente de datos" dropdown alongside Replicon.
--
-- Entity rows in `entity_config` are NOT inserted here: each SAP microservice
-- imports its own entities from app/config/entities.yaml on startup via
-- catalog_service._seed_if_empty(). This file only registers the cartridge
-- header, the DAGs and the semantic vocabulary.
--
-- Safe to re-run: all inserts use ON CONFLICT DO NOTHING / DO UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── SAP SuccessFactors ───────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_successfactors',
    'SAP SuccessFactors',
    '1.0.0',
    'Employee Central — empleados, posiciones, departamentos, compensaciones, centros de coste, tiempo y ausencias.',
    'dag-based',
    'cartridge',
    'raw/sap_successfactors/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        pattern     = EXCLUDED.pattern,
        category    = EXCLUDED.category,
        bronze_path = EXCLUDED.bronze_path,
        updated_at  = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_successfactors', 'sap_successfactors_extract',     'sap_successfactors_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_successfactors', 'sap_successfactors_extract_all', 'sap_successfactors_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',     'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_successfactors', 'headcount activo', 'Número de empleados activos (startDate <= hoy <= endDate)', 'EmpEmployment WHERE isActive = true'),
    ('sap_successfactors', 'turnover',         'Rotación de personal',                                      'User.status changes'),
    ('sap_successfactors', 'evaluación',       'Rating en PerformanceReview',                               'PerformanceReview.overallRating')
ON CONFLICT (cartridge_id, term) DO NOTHING;


-- ── SAP HCM ──────────────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_hcm',
    'SAP HCM',
    '1.0.0',
    'SAP HCM / NetWeaver Gateway — infotypes PA/PT/OM, empleados, organización, ausencias, tiempos.',
    'dag-based',
    'cartridge',
    'raw/sap_hcm/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        pattern     = EXCLUDED.pattern,
        category    = EXCLUDED.category,
        bronze_path = EXCLUDED.bronze_path,
        updated_at  = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_hcm', 'sap_hcm_extract',     'sap_hcm_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_hcm', 'sap_hcm_extract_all', 'sap_hcm_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',     'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_hcm', 'headcount activo', 'Número de empleados activos hoy', 'EmployeeMaster WHERE Endda >= CURRENT_DATE AND Begda <= CURRENT_DATE'),
    ('sap_hcm', 'ausencia',         'Días de ausencia',                'LeaveAbsence.Abwtg')
ON CONFLICT (cartridge_id, term) DO NOTHING;


-- ── SAP S/4HANA ──────────────────────────────────────────────────────────────
INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_s4hana',
    'SAP S/4HANA',
    '1.0.0',
    'ERP core — business partners, clientes, proveedores, ventas, compras, contabilidad y journal entries.',
    'dag-based',
    'cartridge',
    'raw/sap_s4hana/{entity}/load_date={date}/'
)
ON CONFLICT (id) DO UPDATE
    SET name        = EXCLUDED.name,
        version     = EXCLUDED.version,
        description = EXCLUDED.description,
        pattern     = EXCLUDED.pattern,
        category    = EXCLUDED.category,
        bronze_path = EXCLUDED.bronze_path,
        updated_at  = NOW();

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_s4hana', 'sap_s4hana_extract',     'sap_s4hana_extract.py',     'Extrae una entidad en Bronze MinIO (full o incremental)', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_s4hana', 'sap_s4hana_extract_all', 'sap_s4hana_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia',     'on-demand', '["mode","entities"]')
ON CONFLICT (cartridge_id, dag_id) DO NOTHING;

INSERT INTO semantic_terms (cartridge_id, term, definition, maps_to)
VALUES
    ('sap_s4hana', 'business partner', 'Cliente o proveedor unificado',         'BusinessPartner'),
    ('sap_s4hana', 'journal entry',    'Asiento contable en libro mayor',       'JournalEntry')
ON CONFLICT (cartridge_id, term) DO NOTHING;
