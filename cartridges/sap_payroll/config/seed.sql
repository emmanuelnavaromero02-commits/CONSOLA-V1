INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_payroll', 'SAP Payroll', '1.0.0', 'SAP Payroll Connector', 'dag-based', 'cartridge', 'raw/sap_payroll/{entity}/load_date={date}/'
) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_payroll', 'sap_payroll_extract', 'sap_payroll_extract.py', 'Extrae una entidad en Bronze MinIO (full o incremental)', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_payroll', 'sap_payroll_extract_all', 'sap_payroll_extract_all.py', 'Extrae todas las entidades', 'on-demand', '["mode","entities"]')
ON CONFLICT DO NOTHING;

INSERT INTO entity_config (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, enabled, trigger_type)
VALUES
    ('sap_payroll', 'PayrollResult', 'PayrollResult', 'incremental', 'Pernr', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'WageType', 'WageType', 'full', 'Lgart', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'PayrollArea', 'PayrollArea', 'full', 'Abkrs', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'PayrollPeriod', 'PayrollPeriod', 'incremental', 'Abkrs', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'TaxWithholding', 'TaxWithholding', 'incremental', 'Pernr', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'SocialSecurity', 'SocialSecurity', 'incremental', 'Pernr', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'BankTransfer', 'BankTransfer', 'incremental', 'Pernr', 'sap_payroll_extract', '', TRUE, 'manual'),
    ('sap_payroll', 'PayslipSummary', 'PayslipSummary', 'incremental', 'Pernr', 'sap_payroll_extract', '', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE SET display_name = EXCLUDED.display_name;
