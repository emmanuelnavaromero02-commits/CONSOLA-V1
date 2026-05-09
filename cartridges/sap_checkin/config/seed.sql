INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_checkin', 'SAP Check-In Empleados', '1.0.0', 'SAP Check-In Connector', 'dag-based', 'cartridge', 'raw/sap_checkin/{entity}/load_date={date}/'
) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_checkin', 'sap_checkin_extract', 'sap_checkin_extract.py', 'Extrae', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_checkin', 'sap_checkin_extract_all', 'sap_checkin_extract_all.py', 'Extrae', 'on-demand', '["mode","entities"]')
ON CONFLICT DO NOTHING;

INSERT INTO entity_config (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, enabled, trigger_type)
VALUES
    ('sap_checkin', 'EmployeePresence', 'EmployeePresence', 'incremental', 'Pernr', 'sap_checkin_extract', '', TRUE, 'manual'),
    ('sap_checkin', 'AccessEvent', 'AccessEvent', 'incremental', 'Pernr', 'sap_checkin_extract', '', TRUE, 'manual'),
    ('sap_checkin', 'AccessZone', 'AccessZone', 'full', 'ZoneId', 'sap_checkin_extract', '', TRUE, 'manual'),
    ('sap_checkin', 'AccessDevice', 'AccessDevice', 'full', 'DeviceId', 'sap_checkin_extract', '', TRUE, 'manual'),
    ('sap_checkin', 'AbsenceAlert', 'AbsenceAlert', 'incremental', 'Pernr', 'sap_checkin_extract', '', TRUE, 'manual'),
    ('sap_checkin', 'WorkdayCompliance', 'WorkdayCompliance', 'incremental', 'Pernr', 'sap_checkin_extract', '', TRUE, 'manual'),
    ('sap_checkin', 'CapacitySnapshot', 'CapacitySnapshot', 'incremental', 'ZoneId', 'sap_checkin_extract', '', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE SET display_name = EXCLUDED.display_name;
