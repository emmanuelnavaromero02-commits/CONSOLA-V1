INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_hcm', 'SAP HCM Core', '1.0.0', 'SAP HCM on-premise / S4HANA — extrae datos maestros de empleados, estructura organizacional, ausencias y horarios.', 'dag-based', 'cartridge', 'raw/sap_hcm/{entity}/load_date={date}/'
) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_hcm', 'sap_hcm_extract', 'sap_hcm_extract.py', 'Extrae una entidad en Bronze MinIO (full o incremental)', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_hcm', 'sap_hcm_extract_all', 'sap_hcm_extract_all.py', 'Extrae todas las entidades habilitadas en secuencia', 'on-demand', '["mode","entities"]')
ON CONFLICT DO NOTHING;

INSERT INTO entity_config (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, enabled, trigger_type)
VALUES
    ('sap_hcm', 'EmployeeMaster', 'Maestro de Empleados', 'incremental', 'Pernr', 'sap_hcm_extract', 'Asignación organizacional del empleado (infotipo 0001)', TRUE, 'manual'),
    ('sap_hcm', 'PersonalData', 'Datos Personales', 'incremental', 'Pernr', 'sap_hcm_extract', 'Datos personales del empleado', TRUE, 'manual'),
    ('sap_hcm', 'ContractData', 'Datos de Contrato', 'incremental', 'Pernr', 'sap_hcm_extract', 'Datos del contrato del empleado', TRUE, 'manual'),
    ('sap_hcm', 'OrgUnit', 'Unidad Organizacional', 'full', 'ObjId', 'sap_hcm_extract', 'Unidad Organizacional', TRUE, 'manual'),
    ('sap_hcm', 'Position', 'Posición', 'full', 'ObjId', 'sap_hcm_extract', 'Posición', TRUE, 'manual'),
    ('sap_hcm', 'CostCenter', 'Centro de Costos', 'full', 'Kostl', 'sap_hcm_extract', 'Centro de Costos', TRUE, 'manual'),
    ('sap_hcm', 'JobCode', 'Código de Trabajo', 'full', 'ObjId', 'sap_hcm_extract', 'Código de Trabajo', TRUE, 'manual'),
    ('sap_hcm', 'EmployeeActions', 'Acciones de Empleados', 'incremental', 'Pernr', 'sap_hcm_extract', 'Acciones de personal', TRUE, 'manual'),
    ('sap_hcm', 'LeaveAbsence', 'Ausencias', 'incremental', 'Pernr', 'sap_hcm_extract', 'Ausencias y permisos', TRUE, 'manual'),
    ('sap_hcm', 'WorkSchedule', 'Horarios', 'full', 'Pernr', 'sap_hcm_extract', 'Horario de trabajo', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE SET display_name = EXCLUDED.display_name;
