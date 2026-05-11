INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_time', 'SAP Time Management', '1.0.0', 'SAP Time Connector', 'dag-based', 'cartridge', 'raw/sap_time/{entity}/load_date={date}/'
) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_time', 'sap_time_extract', 'sap_time_extract.py', 'Extrae', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_time', 'sap_time_extract_all', 'sap_time_extract_all.py', 'Extrae', 'on-demand', '["mode","entities"]')
ON CONFLICT DO NOTHING;

INSERT INTO entity_config (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, enabled, trigger_type)
VALUES
    ('sap_time', 'TimeEntry', 'TimeEntry', 'incremental', 'Pernr', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'TimeBalance', 'TimeBalance', 'incremental', 'Pernr', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'Attendance', 'Attendance', 'incremental', 'Pernr', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'WorkScheduleRule', 'WorkScheduleRule', 'full', 'Schkz', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'Shift', 'Shift', 'full', 'Schkz', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'PublicHoliday', 'PublicHoliday', 'full', 'Fdate', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'OvertimeRequest', 'OvertimeRequest', 'incremental', 'Pernr', 'sap_time_extract', '', TRUE, 'manual'),
    ('sap_time', 'ClockEntry', 'ClockEntry', 'incremental', 'Pernr', 'sap_time_extract', '', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE SET display_name = EXCLUDED.display_name;
