INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_fi_co', 'SAP FI/CO Finanzas', '1.0.0', 'SAP FI/CO Connector', 'dag-based', 'cartridge', 'raw/sap_fi_co/{entity}/load_date={date}/'
) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_fi_co', 'sap_fi_co_extract', 'sap_fi_co_extract.py', 'Extrae', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_fi_co', 'sap_fi_co_extract_all', 'sap_fi_co_extract_all.py', 'Extrae', 'on-demand', '["mode","entities"]')
ON CONFLICT DO NOTHING;

INSERT INTO entity_config (cartridge_id, entity, display_name, mode, primary_key, dag_id, description, enabled, trigger_type)
VALUES
    ('sap_fi_co', 'GLAccount', 'GLAccount', 'full', 'Saknr', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'CostCenter', 'CostCenter', 'full', 'Kostl', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'CostElement', 'CostElement', 'full', 'Kstar', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'ActualCost', 'ActualCost', 'incremental', 'Belegnr', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'BudgetLine', 'BudgetLine', 'incremental', 'Kostl', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'PayrollPosting', 'PayrollPosting', 'incremental', 'Pernr', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'ProfitCenter', 'ProfitCenter', 'full', 'Prctr', 'sap_fi_co_extract', '', TRUE, 'manual'),
    ('sap_fi_co', 'InternalOrder', 'InternalOrder', 'full', 'Aufnr', 'sap_fi_co_extract', '', TRUE, 'manual')
ON CONFLICT (cartridge_id, entity) DO UPDATE SET display_name = EXCLUDED.display_name;
